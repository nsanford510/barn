"""Polling orchestrator: the single authority over scheduling state (spec sections 7, 8, 16).

Concurrency model: everything runs on one asyncio event loop. Ticks, retry timers, worker
exits, and refresh requests are posted to a mailbox and handled one at a time by ``_run``, so
async handlers never interleave. Codex telemetry updates are applied by a synchronous method
(no awaits), which is atomic on the loop. Workers never mutate orchestrator state directly.
"""

from __future__ import annotations

import asyncio
import collections
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .codex import AppServerSession, ToolExecutor
from .config import ServiceConfig, validate_dispatch_config
from .errors import SymphonyError
from .log import get_logger
from .models import Issue, normalize_state, utc_now, iso
from .prompt import continuation_prompt, render_prompt
from .trackers import TrackerAdapter, TrackerPolicy, build_tracker
from .workflow import WorkflowDefinition, WorkflowWatcher, load_workflow
from .workspace import WorkspaceManager

log = get_logger("orchestrator")

CONTINUATION_DELAY_MS = 1_000
FAILURE_BASE_DELAY_MS = 10_000
RECENT_EVENTS = 50
MAX_TRACKED_ISSUES = 500
WATCH_INTERVAL_S = 1.0


@dataclass
class RunningEntry:
    issue: Issue
    token: str
    started_at: datetime
    started_mono: float
    retry_attempt: int | None
    task: asyncio.Task | None = None
    phase: str = "PreparingWorkspace"
    workspace_path: str | None = None
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    codex_app_server_pid: str | None = None
    last_codex_event: str | None = None
    last_codex_timestamp: datetime | None = None
    last_codex_mono: float | None = None
    last_codex_message: str | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0

    @property
    def identifier(self) -> str:
        return self.issue.identifier


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    due_at: datetime
    token: str
    timer_handle: asyncio.TimerHandle | None
    error: str | None
    issue_url: str | None = None


@dataclass
class IssueDebug:
    issue_id: str
    identifier: str
    url: str | None = None
    workspace_path: str | None = None
    restart_count: int = 0
    last_error: str | None = None
    status: str = "unknown"
    recent_events: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=RECENT_EVENTS))


def dispatch_sort_key(issue: Issue) -> tuple:
    bucketed = isinstance(issue.priority, int) and 1 <= issue.priority <= 4
    created = issue.created_at.timestamp() if issue.created_at else 0.0
    return (0 if bucketed else 1, issue.priority if bucketed else 0,
            issue.created_at is None, created, issue.identifier)


def failure_backoff_ms(attempt: int, max_backoff_ms: int) -> int:
    return min(FAILURE_BASE_DELAY_MS * (2 ** max(attempt - 1, 0)), max_backoff_ms)


class Orchestrator:
    def __init__(self, workflow_path: str, *,
                 tracker_factory: Callable[[ServiceConfig], TrackerAdapter] = build_tracker,
                 session_factory: Callable[..., AppServerSession] = AppServerSession,
                 clock: Callable[[], float] = time.monotonic):
        self.workflow_path = workflow_path
        self.tracker_factory = tracker_factory
        self.session_factory = session_factory
        self.clock = clock

        self.workflow: WorkflowDefinition | None = None
        self.config: ServiceConfig | None = None
        self.workflow_error: str | None = None
        self._watcher: WorkflowWatcher | None = None
        self._tracker: TrackerAdapter | None = None
        self._tracker_error: str | None = None

        # Spec 4.1.8 runtime state.
        self.running: dict[str, RunningEntry] = {}
        self.claimed: set[str] = set()
        self.retry_attempts: dict[str, RetryEntry] = {}
        self.completed: set[str] = set()
        self.codex_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                             "seconds_running": 0.0}
        self.codex_rate_limits: Any = None

        self._stopping: dict[str, asyncio.Task] = {}
        self._debug: collections.OrderedDict[str, IssueDebug] = collections.OrderedDict()
        self._mailbox: asyncio.Queue | None = None
        self._loop_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._tick_handle: asyncio.TimerHandle | None = None
        self._tick_queued = False
        self._background: set[asyncio.Task] = set()
        self._stopped = asyncio.Event()
        self._died = asyncio.Event()
        self.last_tick_at: datetime | None = None
        self.last_dispatch_error: str | None = None

    # ==== properties ============================================================================
    @property
    def poll_interval_ms(self) -> int:
        return self.config.poll_interval_ms if self.config else 30_000

    @property
    def max_concurrent_agents(self) -> int:
        return self.config.agent.max_concurrent_agents if self.config else 0

    # ==== startup / shutdown ====================================================================
    async def start(self) -> None:
        """Validate, run startup cleanup, and begin the event loop. Raises on startup failure."""
        await self.initialize()
        self._loop_task = asyncio.create_task(self._run(), name="symphony-orchestrator")
        self._loop_task.add_done_callback(self._on_loop_done)
        self._watch_task = asyncio.create_task(self._watch_workflow(), name="symphony-watch")
        self._post_tick()

    async def initialize(self) -> None:
        """Startup validation + terminal workspace cleanup, without starting background loops."""
        self.workflow = load_workflow(self.workflow_path)
        self.config = ServiceConfig.from_workflow(self.workflow)
        validate_dispatch_config(self.config)
        self._tracker = self.tracker_factory(self.config)
        self._watcher = WorkflowWatcher(self.workflow_path)
        log.info("service_starting", workflow=self.workflow_path,
                 tracker_kind=self.config.tracker.kind, workspace_root=self.config.workspace_root,
                 poll_interval_ms=self.config.poll_interval_ms,
                 max_concurrent_agents=self.config.agent.max_concurrent_agents)
        await self._startup_terminal_cleanup()
        self._mailbox = asyncio.Queue()

    async def wait_stopped(self) -> None:
        """Returns only if the orchestrator loop dies unexpectedly (abnormal host exit)."""
        await self._died.wait()

    def _on_loop_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        log.error("orchestrator_loop_died", reason=repr(task.exception()))
        self._died.set()

    async def stop(self) -> None:
        if self._stopped.is_set():
            return
        log.info("service_stopping", running=len(self.running), retrying=len(self.retry_attempts))
        if self._tick_handle:
            self._tick_handle.cancel()
        for entry in self.retry_attempts.values():
            if entry.timer_handle:
                entry.timer_handle.cancel()
        self.retry_attempts.clear()
        for t in (self._watch_task, self._loop_task):
            if t:
                t.cancel()
        tasks = [e.task for e in self.running.values() if e.task] + list(self._stopping.values())
        for e in list(self.running.values()):
            self._account_runtime(e)
        self.running.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        self._stopped.set()

    async def _startup_terminal_cleanup(self) -> None:
        assert self.config and self._tracker
        tracker = self._tracker
        try:
            issues = await tracker.fetch_issues_by_states(tracker.terminal_states)
        except SymphonyError as exc:
            log.warning("startup_cleanup_failed", reason=exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("startup_cleanup_failed", reason=repr(exc))
            return
        ws = self._workspace_manager()
        for issue in issues:
            try:
                await ws.remove(issue.identifier, self._ctx(issue))
            except SymphonyError as exc:
                log.warning("workspace_cleanup_failed", reason=exc, **self._ctx(issue))

    # ==== mailbox loop ==========================================================================
    def _post(self, *msg: Any) -> None:
        if self._mailbox is not None:
            self._mailbox.put_nowait(msg)

    def _post_tick(self) -> None:
        if not self._tick_queued:
            self._tick_queued = True
            self._post("tick")

    def request_refresh(self) -> dict[str, Any]:
        coalesced = self._tick_queued
        if self._tick_handle:
            self._tick_handle.cancel()
            self._tick_handle = None
        self._post_tick()
        return {"queued": True, "coalesced": coalesced, "requested_at": iso(utc_now()),
                "operations": ["poll", "reconcile"]}

    async def _run(self) -> None:
        assert self._mailbox
        while True:
            await self._handle(await self._mailbox.get())

    async def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        try:
            if kind == "tick":
                self._tick_queued = False
                await self._on_tick()
            elif kind == "worker_exit":
                self._on_worker_exit(*msg[1:])
            elif kind == "retry":
                await self._on_retry_timer(*msg[1:])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the orchestrator must survive handler bugs
            log.error("orchestrator_handler_crashed", message_kind=kind,
                      trace=traceback.format_exc()[-2000:])
            if kind == "tick":
                self._schedule_tick()

    def _schedule_tick(self) -> None:
        if self._tick_handle:
            self._tick_handle.cancel()
        loop = asyncio.get_running_loop()
        self._tick_handle = loop.call_later(self.poll_interval_ms / 1000, self._post_tick)

    # ==== workflow reload =======================================================================
    async def _watch_workflow(self) -> None:
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            self.maybe_reload()

    def maybe_reload(self, force: bool = False) -> bool:
        """Re-read WORKFLOW.md if it changed. Invalid reloads keep the last good config."""
        if self._watcher is None:
            return False
        if not self._watcher.changed() and not force:
            return False
        try:
            wf = load_workflow(self.workflow_path)
            cfg = ServiceConfig.from_workflow(wf)
            tracker = self.tracker_factory(cfg)
        except SymphonyError as exc:
            self.workflow_error = str(exc)
            log.error("workflow_reload_failed", reason=exc, workflow=self.workflow_path,
                      effect="keeping last known good configuration; dispatch paused")
            return False
        old_interval = self.poll_interval_ms
        self.workflow, self.config, self._tracker = wf, cfg, tracker
        self.workflow_error = None
        log.info("workflow_reloaded", workflow=self.workflow_path,
                 poll_interval_ms=cfg.poll_interval_ms,
                 max_concurrent_agents=cfg.agent.max_concurrent_agents)
        if cfg.poll_interval_ms != old_interval and self._tick_handle is not None:
            self._schedule_tick()
        return True

    # ==== tick ==================================================================================
    async def _on_tick(self) -> None:
        self.last_tick_at = utc_now()
        try:
            self.maybe_reload()
            await self._reconcile_running()
            if not self._preflight_ok():
                return
            assert self._tracker
            tracker = self._tracker
            policy = TrackerPolicy.from_adapter(tracker)
            try:
                issues = await tracker.fetch_issues_by_states(tracker.active_states)
            except Exception as exc:  # noqa: BLE001
                self.last_dispatch_error = f"candidate fetch failed: {exc}"
                log.error("candidate_fetch_failed", reason=exc)
                return
            for issue in sorted(issues, key=dispatch_sort_key):
                if self._available_slots() <= 0:
                    break
                if self._should_dispatch(issue, policy):
                    self._dispatch(issue, attempt=None)
            self.last_dispatch_error = None
        finally:
            self._schedule_tick()

    def _preflight_ok(self) -> bool:
        if self.workflow_error:
            self.last_dispatch_error = self.workflow_error
            log.error("dispatch_validation_failed", reason=self.workflow_error)
            return False
        try:
            assert self.config
            validate_dispatch_config(self.config)
        except SymphonyError as exc:
            self.last_dispatch_error = str(exc)
            log.error("dispatch_validation_failed", reason=exc)
            return False
        return True

    def _available_slots(self) -> int:
        return max(self.max_concurrent_agents - len(self.running), 0)

    def _state_slots_available(self, state: str) -> bool:
        assert self.config
        key = normalize_state(state)
        used = sum(1 for e in self.running.values() if normalize_state(e.issue.state) == key)
        return used < self.config.max_for_state(state)

    def _should_dispatch(self, issue: Issue, policy: TrackerPolicy) -> bool:
        if not (issue.id and issue.identifier and issue.title and issue.state):
            return False
        if not policy.is_active(issue) or not policy.routable(issue):
            return False
        if issue.id in self.running or issue.id in self.claimed or issue.id in self._stopping:
            return False
        return self._available_slots() > 0 and self._state_slots_available(issue.state)

    # ==== reconciliation ========================================================================
    async def _reconcile_running(self) -> None:
        assert self.config
        stall_ms = self.config.codex.stall_timeout_ms
        if stall_ms > 0:
            now = self.clock()
            for issue_id, entry in list(self.running.items()):
                since = entry.last_codex_mono if entry.last_codex_mono is not None \
                    else entry.started_mono
                elapsed_ms = (now - since) * 1000
                if elapsed_ms > stall_ms:
                    log.warning("worker_stalled", elapsed_ms=int(elapsed_ms),
                                session_id=entry.session_id, **self._ctx(entry.issue))
                    self._terminate(issue_id, cleanup=False, retry_error="stalled: no agent "
                                    f"activity for {int(elapsed_ms)}ms")
        if not self.running:
            return
        running_ids = list(self.running)
        assert self._tracker
        tracker = self._tracker
        policy = TrackerPolicy.from_adapter(tracker)
        try:
            refreshed = await tracker.fetch_issues_by_ids(running_ids)
        except Exception as exc:  # noqa: BLE001
            log.warning("reconcile_refresh_failed", reason=exc, effect="keeping workers running")
            return
        returned = set()
        for issue in refreshed:
            returned.add(issue.id)
            entry = self.running.get(issue.id)
            if entry is None:
                continue
            if policy.is_terminal(issue):
                log.info("worker_stopping", reason="terminal_state", state=issue.state,
                         **self._ctx(issue))
                self._terminate(issue.id, cleanup=True, issue=issue)
            elif policy.is_active(issue) and policy.routable(issue):
                entry.issue = issue
            else:
                reason = "not_routable" if policy.is_active(issue) else "non_active_state"
                log.info("worker_stopping", reason=reason, state=issue.state, **self._ctx(issue))
                self._terminate(issue.id, cleanup=False)
        for missing in set(running_ids) - returned:
            entry = self.running.get(missing)
            if entry is not None:
                log.info("worker_stopping", reason="issue_not_visible", **self._ctx(entry.issue))
                self._terminate(missing, cleanup=False)

    def _terminate(self, issue_id: str, *, cleanup: bool, retry_error: str | None = None,
                   issue: Issue | None = None) -> None:
        entry = self.running.pop(issue_id, None)
        if entry is None:
            return
        self._account_runtime(entry)
        task = entry.task
        if task and not task.done():
            task.cancel()
            self._stopping[issue_id] = task
        dbg = self._dbg(entry.issue)
        if retry_error is not None:
            dbg.last_error = retry_error
            self._schedule_retry(issue_id, (entry.retry_attempt or 0) + 1, entry.issue,
                                 error=retry_error)
        else:
            self.claimed.discard(issue_id)
            dbg.status = "released"
        target = issue or entry.issue
        self._spawn_background(self._finish_stop(issue_id, task, target, cleanup))

    async def _finish_stop(self, issue_id: str, task: asyncio.Task | None, issue: Issue,
                           cleanup: bool) -> None:
        try:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        finally:
            if self._stopping.get(issue_id) is task:
                self._stopping.pop(issue_id, None)
        if cleanup:
            await self._cleanup_workspace(issue)

    async def _cleanup_workspace(self, issue: Issue) -> None:
        try:
            await self._workspace_manager().remove(issue.identifier, self._ctx(issue))
        except SymphonyError as exc:
            log.warning("workspace_cleanup_failed", reason=exc, **self._ctx(issue))

    # ==== dispatch ==============================================================================
    def _dispatch(self, issue: Issue, attempt: int | None) -> None:
        assert self.config and self.workflow and self._tracker
        token = uuid.uuid4().hex
        entry = RunningEntry(issue=issue, token=token, started_at=utc_now(),
                             started_mono=self.clock(), retry_attempt=attempt)
        existing = self.retry_attempts.pop(issue.id, None)
        if existing and existing.timer_handle:
            existing.timer_handle.cancel()
        try:
            task = asyncio.create_task(
                self._run_worker(entry, self.config, self.workflow, self._tracker),
                name=f"worker-{issue.identifier}")
        except Exception as exc:  # noqa: BLE001
            log.error("worker_spawn_failed", reason=exc, **self._ctx(issue))
            self._schedule_retry(issue.id, (attempt or 0) + 1, issue,
                                 error="failed to spawn agent")
            return
        entry.task = task
        self.running[issue.id] = entry
        self.claimed.add(issue.id)
        dbg = self._dbg(issue)
        dbg.status = "running"
        if attempt:
            dbg.restart_count += 1
        task.add_done_callback(lambda t, i=issue.id, tok=token: self._worker_done(i, tok, t))
        log.info("dispatched", attempt=attempt, state=issue.state, **self._ctx(issue))

    def _worker_done(self, issue_id: str, token: str, task: asyncio.Task) -> None:
        if task.cancelled():
            reason, error = "cancelled", "worker cancelled"
        elif task.exception() is not None:
            exc = task.exception()
            reason, error = "error", str(exc)
        else:
            reason, error = "normal", None
        self._post("worker_exit", issue_id, token, reason, error)

    def _on_worker_exit(self, issue_id: str, token: str, reason: str, error: str | None) -> None:
        entry = self.running.get(issue_id)
        if entry is None or entry.token != token:
            return  # already terminated by reconciliation/stall handling
        del self.running[issue_id]
        self._account_runtime(entry)
        dbg = self._dbg(entry.issue)
        if reason == "normal":
            self.completed.add(issue_id)
            log.info("worker_completed", turns=entry.turn_count, session_id=entry.session_id,
                     **self._ctx(entry.issue))
            self._schedule_retry(issue_id, 1, entry.issue, error=None, continuation=True)
        else:
            dbg.last_error = error
            attempt = (entry.retry_attempt or 0) + 1
            log.warning("worker_failed", reason=error, attempt=attempt,
                        session_id=entry.session_id, **self._ctx(entry.issue))
            self._schedule_retry(issue_id, attempt, entry.issue,
                                 error=f"worker exited: {error}")

    # ==== retries ===============================================================================
    def _schedule_retry(self, issue_id: str, attempt: int, issue: Issue, *, error: str | None,
                        continuation: bool = False) -> None:
        assert self.config
        old = self.retry_attempts.pop(issue_id, None)
        if old and old.timer_handle:
            old.timer_handle.cancel()
        delay_ms = CONTINUATION_DELAY_MS if continuation else \
            failure_backoff_ms(attempt, self.config.agent.max_retry_backoff_ms)
        token = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay_ms / 1000, self._post, "retry", issue_id, token)
        self.retry_attempts[issue_id] = RetryEntry(
            issue_id=issue_id, identifier=issue.identifier, attempt=attempt,
            due_at_ms=self.clock() * 1000 + delay_ms,
            due_at=utc_now() + timedelta(milliseconds=delay_ms), token=token,
            timer_handle=handle, error=error, issue_url=issue.url)
        self.claimed.add(issue_id)
        self._dbg(issue).status = "retrying"
        log.info("retry_scheduled", attempt=attempt, delay_ms=delay_ms,
                 kind="continuation" if continuation else "failure", error=error,
                 **self._ctx(issue))

    async def _on_retry_timer(self, issue_id: str, token: str) -> None:
        entry = self.retry_attempts.get(issue_id)
        if entry is None or entry.token != token:
            return
        del self.retry_attempts[issue_id]
        placeholder = Issue(id=issue_id, identifier=entry.identifier, title="", state="",
                            dispatchable=False, url=entry.issue_url)
        if self.workflow_error or self._tracker is None:
            self._schedule_retry(issue_id, entry.attempt + 1, placeholder,
                                 error="retry blocked: invalid workflow")
            return
        tracker = self._tracker
        policy = TrackerPolicy.from_adapter(tracker)
        try:
            refreshed = await tracker.fetch_issues_by_ids([issue_id])
        except Exception as exc:  # noqa: BLE001
            log.warning("retry_refresh_failed", reason=exc, issue_id=issue_id,
                        issue_identifier=entry.identifier)
            self._schedule_retry(issue_id, entry.attempt + 1, placeholder,
                                 error="retry refresh failed")
            return
        issue = next((i for i in refreshed if i.id == issue_id), None)
        if issue is None:
            self._release(issue_id, entry.identifier, "issue_not_visible")
            return
        if policy.is_terminal(issue):
            self._release(issue_id, issue.identifier, "terminal_state")
            await self._cleanup_workspace(issue)
            return
        if not (policy.is_active(issue) and policy.routable(issue)):
            self._release(issue_id, issue.identifier, "not_active_or_routable")
            return
        if issue_id in self._stopping or self._available_slots() <= 0 \
                or not self._state_slots_available(issue.state):
            self._schedule_retry(issue_id, entry.attempt + 1, issue,
                                 error="no available orchestrator slots")
            return
        self._dispatch(issue, attempt=entry.attempt)

    def _release(self, issue_id: str, identifier: str, reason: str) -> None:
        self.claimed.discard(issue_id)
        dbg = self._debug.get(identifier)
        if dbg:
            dbg.status = "released"
        log.info("claim_released", reason=reason, issue_id=issue_id, issue_identifier=identifier)

    # ==== worker ================================================================================
    async def _run_worker(self, entry: RunningEntry, cfg: ServiceConfig, wf: WorkflowDefinition,
                          tracker: TrackerAdapter) -> None:
        """One run attempt: workspace -> hooks -> app-server session -> turn loop (spec 16.5)."""
        issue = entry.issue
        ctx = self._ctx(issue)
        policy = TrackerPolicy.from_adapter(tracker)
        secrets = tracker.secret_environment_names()
        ws_mgr = WorkspaceManager(cfg.workspace_root, cfg.hooks, secrets)

        entry.phase = "PreparingWorkspace"
        workspace = await ws_mgr.create_for_issue(issue.identifier, ctx)
        entry.workspace_path = workspace.path
        self._dbg(issue).workspace_path = workspace.path
        session: AppServerSession | None = None
        try:
            await ws_mgr.before_run(workspace.path, ctx)
            entry.phase = "BuildingPrompt"
            first_prompt = render_prompt(wf.prompt_template, issue, entry.retry_attempt)

            entry.phase = "LaunchingAgentProcess"
            tools = ToolExecutor(tracker.agent_tool_specs(), tracker.execute_agent_tool)
            session = self.session_factory(
                cfg.codex, workspace_path=workspace.path, workspace_root=cfg.workspace_root,
                on_event=lambda ev, i=issue.id, t=entry.token: self._on_codex_event(i, t, ev),
                tools=tools, secret_env_names=secrets, auto_approve=cfg.codex.auto_approve)
            entry.phase = "InitializingSession"
            await session.start(issue)

            max_turns = cfg.agent.max_turns
            turn = 1
            while True:
                prompt = first_prompt if turn == 1 else continuation_prompt(issue, turn, max_turns)
                entry.phase = "StreamingTurn"
                await session.run_turn(prompt, issue)
                entry.phase = "Finishing"
                refreshed = await tracker.fetch_issues_by_ids([issue.id])
                if not refreshed:
                    break
                issue = refreshed[0]
                current = self.running.get(issue.id)
                if current is not None and current.token == entry.token:
                    current.issue = issue
                if not (policy.is_active(issue) and policy.routable(issue)):
                    break
                if turn >= max_turns:
                    log.info("max_turns_reached", turns=turn, **ctx)
                    break
                turn += 1
            entry.phase = "Succeeded"
        except asyncio.CancelledError:
            entry.phase = "CanceledByReconciliation"
            raise
        except BaseException:
            entry.phase = "Failed"
            raise
        finally:
            if session is not None:
                await session.stop()
            await ws_mgr.after_run(workspace.path, ctx)

    # ==== codex telemetry =======================================================================
    def _on_codex_event(self, issue_id: str, token: str, ev: dict[str, Any]) -> None:
        entry = self.running.get(issue_id)
        if entry is None or entry.token != token:
            return
        event = ev.get("event")
        entry.last_codex_event = ev.get("method") if event == "notification" else event
        entry.last_codex_timestamp = ev.get("timestamp") or utc_now()
        entry.last_codex_mono = self.clock()
        if ev.get("message"):
            entry.last_codex_message = str(ev["message"])[:500]
        if ev.get("codex_app_server_pid"):
            entry.codex_app_server_pid = ev["codex_app_server_pid"]
        if event == "session_started":
            entry.thread_id, entry.turn_id = ev.get("thread_id"), ev.get("turn_id")
            entry.session_id = ev.get("session_id")
            entry.turn_count += 1
            log.info("session_started", session_id=entry.session_id, turn=entry.turn_count,
                     pid=entry.codex_app_server_pid, **self._ctx(entry.issue))
        elif event in ("turn_completed", "turn_failed", "turn_cancelled", "turn_input_required",
                       "turn_ended_with_error", "startup_failed"):
            log.info(event, session_id=entry.session_id, error=ev.get("error") or ev.get("reason"),
                     **self._ctx(entry.issue))
        usage = ev.get("usage")
        if isinstance(usage, dict):
            self._apply_usage(entry, usage)
        if ev.get("rate_limits") is not None:
            self.codex_rate_limits = ev["rate_limits"]
        self._dbg(entry.issue).recent_events.append({
            "at": iso(entry.last_codex_timestamp), "event": entry.last_codex_event,
            "message": entry.last_codex_message if ev.get("message") else None})

    def _apply_usage(self, entry: RunningEntry, usage: dict[str, int]) -> None:
        """Absolute thread totals -> deltas vs. last reported, so nothing is double-counted."""
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if not isinstance(value, int):
                continue
            last = getattr(entry, f"last_reported_{key}")
            delta = value - last
            if delta > 0:
                self.codex_totals[key] += delta
                setattr(entry, f"codex_{key}", getattr(entry, f"codex_{key}") + delta)
            setattr(entry, f"last_reported_{key}", max(value, last))

    def _account_runtime(self, entry: RunningEntry) -> None:
        self.codex_totals["seconds_running"] += max(self.clock() - entry.started_mono, 0.0)

    # ==== observability =========================================================================
    def _ctx(self, issue: Issue) -> dict[str, str]:
        return {"issue_id": issue.id, "issue_identifier": issue.identifier}

    def _dbg(self, issue: Issue) -> IssueDebug:
        dbg = self._debug.get(issue.identifier)
        if dbg is None:
            dbg = IssueDebug(issue_id=issue.id, identifier=issue.identifier)
            self._debug[issue.identifier] = dbg
            while len(self._debug) > MAX_TRACKED_ISSUES:
                self._debug.popitem(last=False)
        else:
            self._debug.move_to_end(issue.identifier)
        if issue.url:
            dbg.url = issue.url
        return dbg

    def _spawn_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _workspace_manager(self) -> WorkspaceManager:
        assert self.config
        secrets = self._tracker.secret_environment_names() if self._tracker else []
        return WorkspaceManager(self.config.workspace_root, self.config.hooks, secrets)

    def _running_row(self, e: RunningEntry) -> dict[str, Any]:
        return {
            "issue_id": e.issue.id, "issue_identifier": e.identifier, "issue_url": e.issue.url,
            "state": e.issue.state, "session_id": e.session_id, "turn_count": e.turn_count,
            "phase": e.phase, "attempt": e.retry_attempt, "workspace_path": e.workspace_path,
            "codex_app_server_pid": e.codex_app_server_pid,
            "last_event": e.last_codex_event, "last_message": e.last_codex_message or "",
            "started_at": iso(e.started_at), "last_event_at": iso(e.last_codex_timestamp),
            "tokens": {"input_tokens": e.codex_input_tokens,
                       "output_tokens": e.codex_output_tokens,
                       "total_tokens": e.codex_total_tokens},
        }

    def _retry_row(self, r: RetryEntry) -> dict[str, Any]:
        return {"issue_id": r.issue_id, "issue_identifier": r.identifier,
                "issue_url": r.issue_url, "attempt": r.attempt, "due_at": iso(r.due_at),
                "error": r.error}

    def snapshot(self) -> dict[str, Any]:
        """Synchronous runtime snapshot (spec 13.3)."""
        now = self.clock()
        live = sum(max(now - e.started_mono, 0.0) for e in self.running.values())
        totals = dict(self.codex_totals)
        totals["seconds_running"] = round(totals["seconds_running"] + live, 1)
        return {
            "generated_at": iso(utc_now()),
            "counts": {"running": len(self.running), "retrying": len(self.retry_attempts),
                       "claimed": len(self.claimed), "completed": len(self.completed)},
            "running": [self._running_row(e) for e in self.running.values()],
            "retrying": sorted((self._retry_row(r) for r in self.retry_attempts.values()),
                               key=lambda r: r["due_at"] or ""),
            "codex_totals": totals,
            "rate_limits": self.codex_rate_limits,
            "health": {
                "workflow_path": self.workflow_path,
                "workflow_error": self.workflow_error,
                "last_dispatch_error": self.last_dispatch_error,
                "last_tick_at": iso(self.last_tick_at),
                "poll_interval_ms": self.poll_interval_ms,
                "max_concurrent_agents": self.max_concurrent_agents,
            },
        }

    def issue_detail(self, identifier: str) -> dict[str, Any] | None:
        running = next((e for e in self.running.values() if e.identifier == identifier), None)
        retry = next((r for r in self.retry_attempts.values() if r.identifier == identifier), None)
        dbg = self._debug.get(identifier)
        if running is None and retry is None and dbg is None:
            return None
        issue_id = (running.issue.id if running else retry.issue_id if retry else dbg.issue_id)
        status = "running" if running else "retrying" if retry else (dbg.status if dbg else "unknown")
        workspace = (running.workspace_path if running else None) or (dbg.workspace_path if dbg else None)
        if workspace is None and self.config:
            workspace = self._workspace_manager().path_for(identifier)
        return {
            "issue_identifier": identifier,
            "issue_id": issue_id,
            "issue_url": (running.issue.url if running else None) or (dbg.url if dbg else None),
            "status": status,
            "workspace": {"path": workspace},
            "attempts": {"restart_count": dbg.restart_count if dbg else 0,
                         "current_retry_attempt": (retry.attempt if retry else
                                                   running.retry_attempt if running else None)},
            "running": self._running_row(running) if running else None,
            "retry": self._retry_row(retry) if retry else None,
            "logs": {"codex_session_logs": []},
            "recent_events": list(dbg.recent_events) if dbg else [],
            "last_error": dbg.last_error if dbg else None,
            "tracked": {"claimed": issue_id in self.claimed,
                        "completed": issue_id in self.completed},
        }
