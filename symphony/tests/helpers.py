"""Shared test doubles."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

logging.getLogger("symphony").addHandler(logging.NullHandler())
logging.getLogger("symphony").propagate = False

from symphony.errors import AgentError, TrackerError  # noqa: E402
from symphony.models import Issue, ToolResult, ToolSpec  # noqa: E402
from symphony.trackers import register_tracker  # noqa: E402
from symphony.trackers.base import TrackerAdapter  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_SERVER = FIXTURES / "fake_app_server.py"


def make_issue(n: int | str, state: str = "Todo", **kw: Any) -> Issue:
    ident = n if isinstance(n, str) else f"T-{n}"
    defaults = dict(id=f"id-{ident}", identifier=ident, title=f"Issue {ident}", state=state,
                    dispatchable=True)
    defaults.update(kw)
    return Issue(**defaults)


class MemoryTracker(TrackerAdapter):
    """Mutable in-memory tracker shared across adapter rebuilds."""

    kind = "memory"
    default_active_states = ("Todo", "In Progress")
    default_terminal_states = ("Done", "Canceled")
    issues: dict[str, Issue] = {}
    fail_states = False
    fail_ids = False
    calls: list[tuple] = []

    @classmethod
    def reset(cls, issues: list[Issue] = ()):
        cls.issues = {i.id: i for i in issues}
        cls.fail_states = False
        cls.fail_ids = False
        cls.calls = []

    @classmethod
    def put(cls, issue: Issue):
        cls.issues[issue.id] = issue

    async def fetch_issues_by_states(self, state_names):
        names = {s.strip().lower() for s in state_names}
        type(self).calls.append(("states", tuple(sorted(names))))
        if self.fail_states:
            raise TrackerError("boom", category="tracker_request")
        return [i for i in self.issues.values() if i.state.strip().lower() in names]

    async def fetch_issues_by_ids(self, issue_ids):
        ids = list(issue_ids)
        type(self).calls.append(("ids", tuple(ids)))
        if self.fail_ids:
            raise TrackerError("boom", category="tracker_request")
        return [self.issues[i] for i in ids if i in self.issues]

    def agent_tool_specs(self):
        return [ToolSpec("memory_echo", "echo", {"type": "object"})]

    def secret_environment_names(self):
        return ["SYMPHONY_TEST_SECRET"]

    async def execute_agent_tool(self, name, arguments, context):
        return ToolResult.ok({"echo": arguments, "issue": context["issue"].identifier})


register_tracker("memory", MemoryTracker)


class FakeSession:
    """Stand-in for AppServerSession driven by a per-turn behaviour callable.

    behaviour(session, issue, turn_number) -> awaitable or None; raise AgentError to fail.
    """

    instances: list["FakeSession"] = []
    behaviour: Callable | None = None
    start_error: Exception | None = None

    def __init__(self, config, *, workspace_path, workspace_root, on_event, tools=None,
                 secret_env_names=(), auto_approve=True):
        self.config = config
        self.workspace_path = workspace_path
        self.workspace_root = workspace_root
        self.on_event = on_event
        self.tools = tools
        self.secret_env_names = secret_env_names
        self.turns: list[str] = []
        self.stopped = False
        FakeSession.instances.append(self)

    @classmethod
    def reset(cls, behaviour=None, start_error=None):
        cls.instances = []
        cls.behaviour = behaviour
        cls.start_error = start_error

    async def start(self, issue):
        if FakeSession.start_error:
            raise FakeSession.start_error
        return "thr"

    async def run_turn(self, prompt, issue):
        self.turns.append(prompt)
        n = len(self.turns)
        self.on_event({"event": "session_started", "timestamp": datetime.now(timezone.utc),
                       "thread_id": "thr", "turn_id": f"t{n}", "session_id": f"thr-t{n}",
                       "codex_app_server_pid": "4242"})
        if FakeSession.behaviour:
            res = FakeSession.behaviour(self, issue, n)
            if asyncio.iscoroutine(res):
                await res
        self.on_event({"event": "turn_completed", "timestamp": datetime.now(timezone.utc)})

    async def stop(self):
        self.stopped = True


def write_workflow(dirpath: str, front: str, body: str = "Work on {{ issue.identifier }}") -> str:
    path = os.path.join(dirpath, "WORKFLOW.md")
    with open(path, "w") as fh:
        fh.write("---\n" + textwrap.dedent(front).strip() + "\n---\n" + body + "\n")
    return path


class TempDirMixin:
    def make_tmp(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name


__all__ = ["AgentError", "FakeSession", "MemoryTracker", "TempDirMixin", "make_issue",
           "write_workflow", "FAKE_SERVER"]
