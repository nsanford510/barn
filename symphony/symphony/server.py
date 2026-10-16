"""Optional HTTP observability extension (spec 13.7), built on asyncio streams (no deps)."""

from __future__ import annotations

import asyncio
import html
import json
from typing import Any
from urllib.parse import unquote, urlsplit

from .log import get_logger
from .orchestrator import Orchestrator

log = get_logger("http")

_REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 404: "Not Found",
            405: "Method Not Allowed", 413: "Payload Too Large", 500: "Internal Server Error",
            503: "Service Unavailable"}
MAX_BODY = 64 * 1024


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


class StatusServer:
    def __init__(self, orchestrator: Orchestrator, host: str = "127.0.0.1", port: int = 0):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("http_listening", host=self.host, port=self.port)
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # -- routing -----------------------------------------------------------------------------
    def route(self, method: str, path: str) -> tuple[int, str, bytes]:
        orch = self.orchestrator
        if path == "/":
            if method not in ("GET", "HEAD"):
                return self._json(405, _error("method_not_allowed", "use GET"))
            return 200, "text/html; charset=utf-8", render_dashboard(orch.snapshot()).encode()
        if path == "/api/v1/state":
            if method not in ("GET", "HEAD"):
                return self._json(405, _error("method_not_allowed", "use GET"))
            return self._json(200, orch.snapshot())
        if path == "/api/v1/refresh":
            if method != "POST":
                return self._json(405, _error("method_not_allowed", "use POST"))
            return self._json(202, orch.request_refresh())
        if path.startswith("/api/v1/"):
            identifier = unquote(path[len("/api/v1/"):])
            if not identifier or "/" in identifier:
                return self._json(404, _error("not_found", f"no route for {path}"))
            if method not in ("GET", "HEAD"):
                return self._json(405, _error("method_not_allowed", "use GET"))
            detail = orch.issue_detail(identifier)
            if detail is None:
                return self._json(404, _error("issue_not_found",
                                              f"{identifier} is unknown to the current runtime"))
            return self._json(200, detail)
        return self._json(404, _error("not_found", f"no route for {path}"))

    @staticmethod
    def _json(status: int, body: Any) -> tuple[int, str, bytes]:
        return status, "application/json", json.dumps(body, default=str).encode()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), 10)
            parts = request_line.decode("latin-1").split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]
            length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), 10)
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip() or 0)
            if length > MAX_BODY:
                status, ctype, body = self._json(413, _error("payload_too_large", "body too large"))
            else:
                if length:
                    await reader.readexactly(length)
                try:
                    status, ctype, body = self.route(method, urlsplit(target).path)
                except Exception as exc:  # noqa: BLE001 - dashboard failures never crash
                    log.error("http_handler_failed", reason=repr(exc))
                    status, ctype, body = self._json(500, _error("internal_error", str(exc)))
            head = (f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
                    f"Content-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                    "Cache-Control: no-store\r\nConnection: close\r\n\r\n")
            writer.write(head.encode() + (b"" if method == "HEAD" else body))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


def render_dashboard(snap: dict[str, Any]) -> str:
    e = html.escape
    health = snap.get("health", {})
    totals = snap.get("codex_totals", {})

    def rows(items, cols):
        if not items:
            return f'<tr><td colspan="{len(cols)}" class="muted">none</td></tr>'
        out = []
        for it in items:
            cells = []
            for key, fmt in cols:
                val = fmt(it) if callable(fmt) else it.get(key)
                cells.append(f"<td>{e('' if val is None else str(val))}</td>")
            out.append("<tr>" + "".join(cells) + "</tr>")
        return "".join(out)

    run_cols = [("issue_identifier", None), ("state", None), ("phase", None),
                ("turn_count", None), ("session_id", None), ("last_event", None),
                ("last_message", lambda r: (r.get("last_message") or "")[:120]),
                ("tokens", lambda r: r["tokens"]["total_tokens"]), ("started_at", None)]
    retry_cols = [("issue_identifier", None), ("attempt", None), ("due_at", None),
                  ("error", None)]
    alert = ""
    for key in ("workflow_error", "last_dispatch_error"):
        if health.get(key):
            alert += f'<p class="err">{e(key)}: {e(str(health[key]))}</p>'
    th = lambda cols: "".join(f"<th>{e(c[0].replace('_', ' '))}</th>" for c in cols)  # noqa: E731
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5"><title>Symphony</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#777;--line:#e3e3e3;--err:#b00020}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--muted:#999;--line:#333;--err:#ff6b81}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;margin:16px}}
table{{border-collapse:collapse;width:100%;margin-bottom:24px;font-size:13px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
.muted{{color:var(--muted)}} .err{{color:var(--err)}} .wrap{{overflow-x:auto}}
</style></head><body>
<h1>Symphony</h1>
<p class="muted">generated {e(str(snap.get('generated_at')))} · last tick {e(str(health.get('last_tick_at')))}
· poll {e(str(health.get('poll_interval_ms')))}ms · max agents {e(str(health.get('max_concurrent_agents')))}</p>
{alert}
<p>tokens in {totals.get('input_tokens', 0)} · out {totals.get('output_tokens', 0)}
· total {totals.get('total_tokens', 0)} · runtime {totals.get('seconds_running', 0)}s</p>
<h2>Running ({len(snap.get('running', []))})</h2>
<div class="wrap"><table><tr>{th(run_cols)}</tr>{rows(snap.get('running', []), run_cols)}</table></div>
<h2>Retry queue ({len(snap.get('retrying', []))})</h2>
<div class="wrap"><table><tr>{th(retry_cols)}</tr>{rows(snap.get('retrying', []), retry_cols)}</table></div>
<h2>Rate limits</h2><pre>{e(json.dumps(snap.get('rate_limits'), indent=2, default=str))}</pre>
</body></html>"""
