"""Codex app-server client (spec section 10).

Targets the Codex app-server v2 protocol (verified against codex-cli 0.162.1's generated
schema): newline-delimited JSON-RPC messages over the child's stdio, without a ``jsonrpc``
field. stdout carries the protocol; stderr is diagnostic only and is logged separately.

Handshake: ``initialize`` -> ``initialized`` -> ``thread/start`` -> (best effort)
``thread/name/set`` -> ``turn/start`` per turn, completing on ``turn/completed``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import signal
from dataclasses import dataclass
from typing import Any, Callable

from . import __version__
from .config import CodexConfig
from .errors import AgentError
from .log import get_logger
from .models import Issue, ToolResult, ToolSpec, utc_now
from .workspace import child_env, ensure_contained

log = get_logger("codex")

MAX_LINE_BYTES = 10 * 1024 * 1024
_EOF = object()

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class TurnResult:
    thread_id: str
    turn_id: str
    status: str


class ToolExecutor:
    """Binds one session to one adapter snapshot (spec 10.5)."""

    def __init__(self, specs: list[ToolSpec], execute: Callable[..., Any] | None):
        self.specs = list(specs)
        self.names = {s.name for s in self.specs}
        self._execute = execute

    async def run(self, name: str, arguments: Any, issue: Issue) -> ToolResult:
        if name not in self.names or self._execute is None:
            return ToolResult.fail("unsupported_tool", f"unsupported tool: {name}")
        try:
            return await self._execute(name, arguments, {"issue": issue})
        except Exception as exc:  # adapter bugs must not stall the session
            return ToolResult.fail("tool_execution_error", f"{exc.__class__.__name__}: {exc}")


# -- telemetry extraction ---------------------------------------------------------------------

def _pick_int(d: dict, *names: str) -> int | None:
    for n in names:
        v = d.get(n)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def _usage_from(d: Any) -> dict[str, int] | None:
    if not isinstance(d, dict):
        return None
    inp = _pick_int(d, "inputTokens", "input_tokens", "prompt_tokens")
    out = _pick_int(d, "outputTokens", "output_tokens", "completion_tokens")
    tot = _pick_int(d, "totalTokens", "total_tokens")
    if inp is None and out is None and tot is None:
        return None
    inp, out = inp or 0, out or 0
    return {"input_tokens": inp, "output_tokens": out,
            "total_tokens": tot if tot is not None else inp + out}


def extract_absolute_usage(method: str, params: Any) -> dict[str, int] | None:
    """Absolute thread totals only; delta payloads (``last``/``last_token_usage``) are ignored."""
    if not isinstance(params, dict):
        return None
    if method == "thread/tokenUsage/updated":
        return _usage_from((params.get("tokenUsage") or {}).get("total"))
    # Legacy wrapper events: {"msg": {"type": "token_count", "info": {"total_token_usage": ...}}}
    msg = params.get("msg") if isinstance(params.get("msg"), dict) else params
    info = msg.get("info") if isinstance(msg, dict) else None
    if isinstance(info, dict) and "total_token_usage" in info:
        return _usage_from(info.get("total_token_usage"))
    return None


def extract_rate_limits(method: str, params: Any) -> Any:
    if not isinstance(params, dict):
        return None
    if method == "account/rateLimits/updated":
        return params.get("rateLimits")
    msg = params.get("msg") if isinstance(params.get("msg"), dict) else params
    if isinstance(msg, dict) and msg.get("rate_limits") is not None:
        return msg.get("rate_limits")
    return None


def summarize(method: str, params: Any) -> str:
    """Humanized one-line summary; observability only."""
    p = params if isinstance(params, dict) else {}
    item = p.get("item") if isinstance(p.get("item"), dict) else {}
    itype = item.get("type")
    if method == "item/completed" and itype == "agentMessage":
        return str(item.get("text", ""))[:300]
    if method in ("item/started", "item/completed") and itype == "commandExecution":
        verb = "running" if method == "item/started" else "ran"
        return f"{verb}: {str(item.get('command', ''))[:200]}"
    if method in ("item/started", "item/completed") and itype == "fileChange":
        return f"file change ({len(item.get('changes') or [])} files)"
    if method in ("item/started", "item/completed") and itype:
        return f"{method.split('/')[-1]} {itype}"
    if method == "turn/completed":
        turn = p.get("turn") or {}
        err = (turn.get("error") or {}).get("message")
        return f"turn {turn.get('status')}" + (f": {err}" if err else "")
    if method == "error":
        return f"error: {((p.get('error') or {}).get('message') or '')[:300]}"
    if method == "turn/plan/updated":
        return "plan updated"
    return method


class AppServerSession:
    """One live app-server subprocess bound to one issue workspace."""

    def __init__(self, config: CodexConfig, *, workspace_path: str, workspace_root: str,
                 on_event: EventCallback, tools: ToolExecutor | None = None,
                 secret_env_names: list[str] | tuple[str, ...] = (),
                 auto_approve: bool = True):
        self.config = config
        self.workspace_path = workspace_path
        self.workspace_root = workspace_root
        self.on_event = on_event
        self.tools = tools or ToolExecutor([], None)
        self.secret_env_names = tuple(secret_env_names)
        self.auto_approve = auto_approve

        self.proc: asyncio.subprocess.Process | None = None
        self.pid: str | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.issue: Issue | None = None
        self._ids = itertools.count(1)
        self._pending: dict[Any, asyncio.Future] = {}
        self._notifications: asyncio.Queue = asyncio.Queue()
        self._tasks: set[asyncio.Task] = set()
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._closed = False
        self._input_required: str | None = None

    # -- events ------------------------------------------------------------------------------
    def emit(self, event: str, **payload: Any) -> None:
        msg = {"event": event, "timestamp": utc_now(), "codex_app_server_pid": self.pid}
        if self.thread_id and self.turn_id:
            msg["session_id"] = f"{self.thread_id}-{self.turn_id}"
        msg.update({k: v for k, v in payload.items() if v is not None})
        try:
            self.on_event(msg)
        except Exception as exc:  # observability must never break the protocol loop
            log.warning("event_callback_failed", reason=exc)

    # -- lifecycle ---------------------------------------------------------------------------
    async def start(self, issue: Issue) -> str:
        """Launch, initialize, and create the thread. Returns the thread id."""
        self.issue = issue
        try:
            real = ensure_contained(self.workspace_root, self.workspace_path)
        except Exception as exc:
            raise AgentError(str(exc), category="invalid_workspace_cwd") from exc
        if str(real) != os.path.realpath(self.workspace_path) or not os.path.isdir(real):
            raise AgentError(f"invalid workspace cwd {self.workspace_path}",
                             category="invalid_workspace_cwd")
        cwd = str(real)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                "bash", "-lc", self.config.command, cwd=cwd,
                env=child_env(self.secret_env_names),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=MAX_LINE_BYTES, start_new_session=True)
        except FileNotFoundError as exc:
            self.emit("startup_failed", error="bash not found")
            raise AgentError("bash not found", category="codex_not_found") from exc
        self.pid = str(self.proc.pid)
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr = asyncio.create_task(self._read_stderr())
        try:
            await self.request("initialize", {
                "clientInfo": {"name": "symphony", "title": "Symphony", "version": __version__},
                "capabilities": {"experimentalApi": True},
            })
            await self.notify("initialized")
            params: dict[str, Any] = {
                "cwd": cwd,
                "approvalPolicy": self.config.approval_policy,
                "sandbox": self.config.thread_sandbox,
                "serviceName": "symphony",
            }
            if self.config.model:
                params["model"] = self.config.model
            if self.tools.specs:
                params["dynamicTools"] = [
                    {"type": "function", "name": s.name, "description": s.description,
                     "inputSchema": s.input_schema} for s in self.tools.specs]
            result = await self.request("thread/start", params)
            thread = (result or {}).get("thread") or {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise AgentError("thread/start returned no thread id", category="response_error")
            self.thread_id = thread_id
        except AgentError as exc:
            category = exc.category
            if category == "port_exit" and self.proc.returncode == 127:
                category = "codex_not_found"
            self.emit("startup_failed", error=exc.message, reason=category)
            await self.stop()
            raise AgentError(exc.message, category=category) from exc
        try:
            await self.request("thread/name/set", {
                "threadId": self.thread_id, "name": f"{issue.identifier}: {issue.title}"[:200]})
        except AgentError as exc:
            log.debug("thread_name_set_failed", reason=exc.message)
        return self.thread_id

    async def run_turn(self, prompt: str, issue: Issue) -> TurnResult:
        if not self.thread_id:
            raise AgentError("session not started", category="response_error")
        self.issue = issue
        self._input_required = None
        result = await self.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
            "cwd": self.workspace_path,
            "approvalPolicy": self.config.approval_policy,
            "sandboxPolicy": self.config.turn_sandbox_for(self.workspace_path),
        })
        turn = (result or {}).get("turn") or {}
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise AgentError("turn/start returned no turn id", category="response_error")
        self.turn_id = turn_id
        self.emit("session_started", thread_id=self.thread_id, turn_id=turn_id)

        timeout = self.config.turn_timeout_ms / 1000
        while True:
            try:
                msg = await asyncio.wait_for(self._notifications.get(), timeout)
            except asyncio.TimeoutError:
                self.emit("turn_ended_with_error", reason="turn_timeout")
                raise AgentError(f"no app-server output for {self.config.turn_timeout_ms}ms",
                                 category="turn_timeout")
            if msg is _EOF:
                self.emit("turn_ended_with_error", reason="port_exit")
                raise AgentError("app-server exited during turn", category="port_exit",
                                 exit_code=self.proc.returncode if self.proc else None)
            if isinstance(msg, tuple) and msg[0] == "input_required":
                raise AgentError(f"turn requested user input: {msg[1]}",
                                 category="turn_input_required")
            method, params = msg
            if method != "turn/completed":
                continue
            done = (params or {}).get("turn") or {}
            if done.get("id") not in (None, turn_id):
                continue
            status = done.get("status")
            error = (done.get("error") or {}).get("message")
            if status == "completed":
                self.emit("turn_completed", status=status)
                return TurnResult(self.thread_id, turn_id, status)
            if status == "interrupted":
                self.emit("turn_cancelled", error=error)
                raise AgentError(error or "turn interrupted", category="turn_cancelled")
            self.emit("turn_failed", error=error, status=status)
            raise AgentError(error or f"turn ended with status {status}", category="turn_failed")

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        proc = self.proc
        if proc and proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            _signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                _signal_group(proc, signal.SIGKILL)
                await proc.wait()
        for t in (self._reader, self._stderr):
            if t and not t.done():
                t.cancel()
        self._fail_pending(AgentError("session stopped", category="port_exit"))

    # -- JSON-RPC plumbing -------------------------------------------------------------------
    async def _send(self, message: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin or self.proc.returncode is not None:
            raise AgentError("app-server is not running", category="port_exit")
        data = (json.dumps(message, separators=(",", ":"), default=str) + "\n").encode()
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AgentError("app-server stdin closed", category="port_exit") from exc

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, self.config.read_timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise AgentError(f"{method} timed out after {self.config.read_timeout_ms}ms",
                             category="response_timeout")
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    async def _respond(self, req_id: Any, result: Any = None, error: dict | None = None) -> None:
        msg: dict[str, Any] = {"id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            await self._send(msg)
        except AgentError:
            pass

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self.emit("malformed", error=f"line exceeded {MAX_LINE_BYTES} bytes")
                    break
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    self.emit("malformed", message=text[:200])
                    continue
                if not isinstance(msg, dict):
                    self.emit("malformed", message=text[:200])
                    continue
                self._dispatch(msg)
        finally:
            if self.proc and self.proc.returncode is None:
                try:
                    await asyncio.wait_for(self.proc.wait(), 1)
                except asyncio.TimeoutError:
                    pass
            self._fail_pending(AgentError("app-server exited", category="port_exit"))
            self._notifications.put_nowait(_EOF)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            try:
                line = await self.proc.stderr.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue
            if not line:
                return
            log.debug("codex_stderr", pid=self.pid,
                      line=line.decode("utf-8", "replace").rstrip()[:500])

    def _dispatch(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if method is None and "id" in msg:
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    err = msg.get("error") or {}
                    fut.set_exception(AgentError(
                        f"app-server error {err.get('code')}: {err.get('message')}",
                        category="response_error"))
                else:
                    fut.set_result(msg.get("result"))
            return
        if not isinstance(method, str):
            self.emit("other_message", message=str(msg)[:200])
            return
        params = msg.get("params")
        if "id" in msg:
            task = asyncio.create_task(self._handle_server_request(msg["id"], method, params))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        self._handle_notification(method, params)

    def _handle_notification(self, method: str, params: Any) -> None:
        usage = extract_absolute_usage(method, params)
        rate_limits = extract_rate_limits(method, params)
        self.emit("notification", method=method, message=summarize(method, params),
                  usage=usage, rate_limits=rate_limits)
        self._notifications.put_nowait((method, params))

    async def _handle_server_request(self, req_id: Any, method: str, params: Any) -> None:
        p = params if isinstance(params, dict) else {}
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            decision = "acceptForSession" if self.auto_approve else "decline"
            self.emit("approval_auto_approved" if self.auto_approve else "approval_declined",
                      method=method, message=summarize(method, p))
            await self._respond(req_id, {"decision": decision})
        elif method in ("execCommandApproval", "applyPatchApproval"):
            decision = "approved_for_session" if self.auto_approve else "denied"
            self.emit("approval_auto_approved" if self.auto_approve else "approval_declined",
                      method=method)
            await self._respond(req_id, {"decision": decision})
        elif method == "item/permissions/requestApproval":
            # Never widen the configured sandbox at runtime: grant no additional permissions.
            self.emit("notification", method=method, message="additional permissions declined")
            await self._respond(req_id, {"permissions": {}, "scope": "turn"})
        elif method == "item/tool/requestUserInput":
            # Policy: user input is never available in unattended runs -> fail the turn.
            questions = [q.get("question") for q in p.get("questions") or [] if isinstance(q, dict)]
            self.emit("turn_input_required", message="; ".join(map(str, questions))[:300])
            await self._respond(req_id, {"answers": {}})
            self._input_required = "; ".join(map(str, questions)) or "user input requested"
            self._notifications.put_nowait(("input_required", self._input_required))
        elif method == "mcpServer/elicitation/request":
            self.emit("turn_input_required", method=method, message="MCP elicitation declined")
            await self._respond(req_id, {"action": "decline"})
        elif method == "item/tool/call":
            await self._handle_tool_call(req_id, p)
        else:
            self.emit("unsupported_request", method=method)
            await self._respond(req_id, error={"code": -32601,
                                               "message": f"Symphony does not support {method}"})

    async def _handle_tool_call(self, req_id: Any, p: dict[str, Any]) -> None:
        name = p.get("tool")
        if not isinstance(name, str) or name not in self.tools.names:
            self.emit("unsupported_tool_call", tool=name)
            result = ToolResult.fail("unsupported_tool", f"unsupported tool: {name}")
        else:
            assert self.issue is not None
            result = await self.tools.run(name, p.get("arguments"), self.issue)
            self.emit("tool_call_completed", tool=name, success=result.success)
        text = json.dumps(result.output, default=str)
        await self._respond(req_id, {"contentItems": [{"type": "inputText", "text": text}],
                                     "success": bool(result.success)})


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass
