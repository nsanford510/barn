"""Shared normalization helpers for adapters (spec 11.3)."""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..models import BlockerRef, Issue, normalize_label, parse_rfc3339


class MalformedRecord(ValueError):
    pass


def _req_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise MalformedRecord(f"missing required field {key!r}")
    return value


def _opt_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def normalize_labels(values: Iterable[Any] | None) -> tuple[str, ...]:
    out: list[str] = []
    for v in values or ():
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            continue
        lbl = normalize_label(v)
        if lbl and lbl not in out:
            out.append(lbl)
    return tuple(out)


def normalize_blockers(values: Iterable[Any] | None) -> tuple[BlockerRef, ...]:
    out: list[BlockerRef] = []
    for v in values or ():
        if not isinstance(v, dict):
            continue
        ref = BlockerRef(id=_opt_str(v.get("id")), identifier=_opt_str(v.get("identifier")),
                         state=_opt_str(v.get("state")))
        if ref.id or ref.identifier:
            out.append(ref)
    return tuple(out)


def safe_native_ref(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None


def build_issue(raw: dict, *, dispatchable: Any) -> Issue:
    """Build a normalized Issue; raises MalformedRecord when required fields are unusable."""
    if not isinstance(dispatchable, bool):
        raise MalformedRecord("dispatchable must be explicit")
    return Issue(
        id=_req_str(raw, "id"),
        identifier=_req_str(raw, "identifier"),
        title=_req_str(raw, "title"),
        state=_req_str(raw, "state"),
        dispatchable=dispatchable,
        description=_opt_str(raw.get("description")),
        priority=_opt_int(raw.get("priority")),
        branch_name=_opt_str(raw.get("branch_name")),
        url=_opt_str(raw.get("url")),
        assignee_id=_opt_str(raw.get("assignee_id")),
        labels=normalize_labels(raw.get("labels")),
        blocked_by=normalize_blockers(raw.get("blocked_by")),
        native_ref=safe_native_ref(raw.get("native_ref")),
        created_at=parse_rfc3339(raw.get("created_at")),
        updated_at=parse_rfc3339(raw.get("updated_at")),
    )
