"""Core domain model (spec section 4)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def normalize_state(state: str | None) -> str:
    """Normalized issue state: trimmed + lowercased (spec 4.2)."""
    return (state or "").strip().lower()


def normalize_label(label: Any) -> str:
    return str(label).strip().lower() if label is not None else ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: Any) -> datetime | None:
    """Parse an RFC 3339 instant; unusable values normalize to None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@dataclass(frozen=True)
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class Issue:
    """Normalized schedulable work item (spec 4.1.1)."""

    id: str
    identifier: str
    title: str
    state: str
    dispatchable: bool
    description: str | None = None
    priority: int | None = None
    branch_name: str | None = None
    url: str | None = None
    assignee_id: str | None = None
    labels: tuple[str, ...] = ()
    blocked_by: tuple[BlockerRef, ...] = ()
    native_ref: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_template_dict(self) -> dict[str, Any]:
        """All fields with string keys; nested lists/maps preserved (spec 12.2)."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "state": self.state,
            "branch_name": self.branch_name,
            "url": self.url,
            "assignee_id": self.assignee_id,
            "labels": list(self.labels),
            "blocked_by": [dataclasses.asdict(b) for b in self.blocked_by],
            "dispatchable": self.dispatchable,
            "native_ref": self.native_ref,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


@dataclass
class Workspace:
    path: str
    workspace_key: str
    created_now: bool


@dataclass
class TokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolResult:
    """Result of a provider-native agent tool call (spec 10.5)."""

    success: bool
    output: Any = None

    @classmethod
    def ok(cls, output: Any) -> "ToolResult":
        return cls(True, output)

    @classmethod
    def fail(cls, category: str, message: str, **extra) -> "ToolResult":
        return cls(False, {"error": {"category": category, "message": message, **extra}})


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
