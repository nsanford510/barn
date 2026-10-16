"""Issue tracker adapter contract (spec section 11)."""

from __future__ import annotations

import abc
from typing import Any, Iterable

from ..config import ServiceConfig
from ..models import Issue, ToolResult, ToolSpec, normalize_label, normalize_state


class TrackerAdapter(abc.ABC):
    """Portable read kernel plus optional provider-native agent tools."""

    kind: str = ""
    default_active_states: tuple[str, ...] | None = None
    default_terminal_states: tuple[str, ...] | None = None

    def __init__(self, config: ServiceConfig):
        self.config = config
        self.active_states = tuple(config.tracker.active_states
                                   if config.tracker.active_states is not None
                                   else (self.default_active_states or ()))
        self.terminal_states = tuple(config.tracker.terminal_states
                                     if config.tracker.terminal_states is not None
                                     else (self.default_terminal_states or ()))

    # -- required reads ---------------------------------------------------------------------
    @abc.abstractmethod
    async def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        ...

    @abc.abstractmethod
    async def fetch_issues_by_ids(self, issue_ids: Iterable[str]) -> list[Issue]:
        ...

    # -- optional provider-native tool extension -------------------------------------------
    def agent_tool_specs(self) -> list[ToolSpec]:
        return []

    def secret_environment_names(self) -> list[str]:
        return []

    async def execute_agent_tool(self, name: str, arguments: Any,
                                 context: dict[str, Any]) -> ToolResult:
        return ToolResult.fail("unsupported_tool", f"unsupported tool: {name}")

    async def aclose(self) -> None:
        return None


class TrackerPolicy:
    """State and label rules the scheduler applies on top of adapter output."""

    def __init__(self, active_states: Iterable[str], terminal_states: Iterable[str],
                 required_labels: Iterable[str]):
        self.active = {normalize_state(s) for s in active_states if normalize_state(s)}
        self.terminal = {normalize_state(s) for s in terminal_states if normalize_state(s)}
        self.required_labels = tuple(required_labels)

    @classmethod
    def from_adapter(cls, adapter: TrackerAdapter) -> "TrackerPolicy":
        return cls(adapter.active_states, adapter.terminal_states,
                   adapter.config.tracker.required_labels)

    def is_active(self, issue: Issue) -> bool:
        s = normalize_state(issue.state)
        return s in self.active and s not in self.terminal

    def is_terminal(self, issue: Issue) -> bool:
        return normalize_state(issue.state) in self.terminal

    def labels_match(self, issue: Issue) -> bool:
        have = {normalize_label(lbl) for lbl in issue.labels}
        # A blank configured label matches no issue.
        return all(lbl and lbl in have for lbl in self.required_labels)

    def routable(self, issue: Issue) -> bool:
        """``issue_routable``: adapter dispatchable + required labels (spec 8.2)."""
        return bool(issue.dispatchable) and self.labels_match(issue)
