"""Local file tracker (``tracker.kind: file``) for credential-free local runs and demos.

The issue file is YAML or JSON: either a list of issues or a map with an ``issues`` list. It is
re-read on every call, so editing it is how an operator changes tracker state. Profile in README.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..config import resolve_path
from ..errors import ConfigError, TrackerError
from ..log import get_logger
from ..models import Issue, ToolResult, ToolSpec, normalize_state
from .base import TrackerAdapter
from .normalize import MalformedRecord, build_issue

log = get_logger("tracker.file")

UPDATE_TOOL = ToolSpec(
    name="file_tracker_update",
    description=(
        "Update the current issue in Symphony's local file tracker: change its state and/or "
        "append a comment. Only the issue this session is working on can be modified."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "New tracker state name."},
            "comment": {"type": "string", "description": "Comment text to append."},
        },
        "additionalProperties": False,
    },
)

_lock = asyncio.Lock()


class FileTracker(TrackerAdapter):
    kind = "file"
    default_active_states = ("Todo", "In Progress")
    default_terminal_states = ("Done", "Canceled", "Cancelled")

    def __init__(self, config):
        super().__init__(config)
        raw = config.tracker.provider.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise TrackerError("tracker.provider.path is required for the file tracker",
                               category="invalid_tracker_config")
        try:
            self.path = resolve_path(raw.strip(), Path(config.workflow_dir))
        except ConfigError as exc:
            raise TrackerError(exc.message, category="invalid_tracker_config") from exc

    def _read_raw(self) -> tuple[Any, list]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TrackerError(f"cannot read issue file {self.path}: {exc}",
                               category="tracker_request") from exc
        try:
            doc = yaml.safe_load(text) if text.strip() else []
        except yaml.YAMLError as exc:
            raise TrackerError(f"issue file is not valid YAML/JSON: {exc}",
                               category="tracker_response") from exc
        items = doc.get("issues") if isinstance(doc, dict) else doc
        if items is None:
            items = []
        if not isinstance(items, list):
            raise TrackerError("issue file must contain a list of issues",
                               category="tracker_response")
        return doc, items

    def _normalize(self, raw: Any) -> Issue:
        if not isinstance(raw, dict):
            raise MalformedRecord("issue entry is not a map")
        data = dict(raw)
        data.setdefault("id", data.get("identifier"))
        for key in ("created_at", "updated_at"):
            if hasattr(data.get(key), "isoformat"):
                data[key] = data[key].isoformat()
        dispatchable = data.get("dispatchable", True)
        terminal = {normalize_state(s) for s in self.terminal_states}
        for b in data.get("blocked_by") or []:
            if isinstance(b, dict) and normalize_state(b.get("state")) not in terminal:
                dispatchable = False
        data["native_ref"] = {"provider": "file", "path": str(self.path)}
        return build_issue(data, dispatchable=dispatchable)

    async def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        wanted = {normalize_state(s) for s in state_names if normalize_state(s)}
        if not wanted:
            return []
        _, items = self._read_raw()
        out = []
        for raw in items:
            try:
                issue = self._normalize(raw)
            except MalformedRecord as exc:
                log.warning("tracker_record_omitted", reason=exc)
                continue
            if normalize_state(issue.state) in wanted:
                out.append(issue)
        return out

    async def fetch_issues_by_ids(self, issue_ids: Iterable[str]) -> list[Issue]:
        ids = set(i for i in issue_ids if i)
        if not ids:
            return []
        _, items = self._read_raw()
        out: dict[str, Issue] = {}
        for raw in items:
            rid = raw.get("id", raw.get("identifier")) if isinstance(raw, dict) else None
            if rid is not None and str(rid) in ids:
                try:
                    out[str(rid)] = self._normalize(raw)
                except MalformedRecord as exc:
                    raise TrackerError(f"malformed issue {rid}: {exc}",
                                       category="tracker_response") from exc
        return list(out.values())

    def agent_tool_specs(self) -> list[ToolSpec]:
        return [UPDATE_TOOL]

    async def execute_agent_tool(self, name: str, arguments: Any,
                                 context: dict[str, Any]) -> ToolResult:
        if name != UPDATE_TOOL.name:
            return ToolResult.fail("unsupported_tool", f"unsupported tool: {name}")
        if not isinstance(arguments, dict):
            return ToolResult.fail("invalid_arguments", "arguments must be an object")
        state, comment = arguments.get("state"), arguments.get("comment")
        if state is None and comment is None:
            return ToolResult.fail("invalid_arguments", "provide 'state' and/or 'comment'")
        if (state is not None and not isinstance(state, str)) or (
                comment is not None and not isinstance(comment, str)):
            return ToolResult.fail("invalid_arguments", "'state' and 'comment' must be strings")
        issue: Issue = context["issue"]
        async with _lock:
            try:
                doc, items = self._read_raw()
            except TrackerError as exc:
                return ToolResult.fail(exc.category, exc.message)
            for raw in items:
                if isinstance(raw, dict) and str(raw.get("id", raw.get("identifier"))) == issue.id:
                    if state is not None:
                        raw["state"] = state
                    if comment is not None:
                        raw.setdefault("comments", []).append(comment)
                    self._write(doc)
                    return ToolResult.ok({"id": issue.id, "state": raw.get("state")})
        return ToolResult.fail("not_found", f"issue {issue.id} is not in the issue file")

    def _write(self, doc: Any) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".issues-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if self.path.suffix == ".json":
                json.dump(doc, fh, indent=2, default=str)
            else:
                yaml.safe_dump(doc, fh, sort_keys=False)
        os.replace(tmp, self.path)
