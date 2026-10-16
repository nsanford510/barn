"""Command-line host: ``python -m symphony [path-to-WORKFLOW.md] [--port N]`` (spec 17.7)."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from . import __version__
from .errors import SymphonyError
from .log import configure_logging, get_logger
from .orchestrator import Orchestrator
from .server import StatusServer
from .workflow import resolve_workflow_path

log = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="symphony",
                                description="Orchestrate coding agents against tracker issues.")
    p.add_argument("workflow", nargs="?", default=None,
                   help="path to WORKFLOW.md (default: ./WORKFLOW.md)")
    p.add_argument("--port", type=int, default=None,
                   help="enable the HTTP dashboard/API on this port (overrides server.port; "
                        "0 = ephemeral)")
    p.add_argument("--host", default=None, help="HTTP bind host (default 127.0.0.1)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--log-file", default=None, help="also write logs to this file")
    p.add_argument("--version", action="version", version=f"symphony {__version__}")
    return p


async def run(args: argparse.Namespace) -> int:
    # Install handlers first so a signal during startup still triggers a clean shutdown.
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_requested.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass
    path = resolve_workflow_path(args.workflow)
    if not path.is_file():
        log.error("startup_failed", reason="missing_workflow_file", workflow=str(path))
        print(f"symphony: workflow file not found: {path}", file=sys.stderr)
        return 1
    orch = Orchestrator(str(path))
    try:
        await orch.start()
    except SymphonyError as exc:
        log.error("startup_failed", reason=exc, workflow=str(path))
        print(f"symphony: startup failed: {exc}", file=sys.stderr)
        return 1

    server = None
    assert orch.config
    port = args.port if args.port is not None else orch.config.server.port
    if port is not None:
        server = StatusServer(orch, host=args.host or orch.config.server.host, port=port)
        try:
            await server.start()
        except OSError as exc:
            log.error("startup_failed", reason=f"http bind failed: {exc}")
            await orch.stop()
            return 1

    waiter = asyncio.create_task(orch.wait_stopped())
    stopper = asyncio.create_task(stop_requested.wait())
    await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
    abnormal = waiter.done() and not stop_requested.is_set()
    for t in (waiter, stopper):
        t.cancel()
    if server:
        await server.stop()
    await orch.stop()
    log.info("service_stopped", abnormal=abnormal)
    return 1 if abnormal else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, args.log_file)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
