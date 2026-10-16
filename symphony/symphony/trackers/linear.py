"""Linear tracker adapter (``tracker.kind: linear``). Profile documented in README.md."""

from __future__ import annotations

import json
from typing import Any, Iterable

import httpx

from ..config import ServiceConfig, env_reference, resolve_env_value
from ..errors import TrackerError
from ..log import get_logger
from ..models import Issue, ToolResult, ToolSpec, normalize_state
from .base import TrackerAdapter
from .normalize import MalformedRecord, build_issue

log = get_logger("tracker.linear")

DEFAULT_ENDPOINT = "https://api.linear.app/graphql"
DEFAULT_API_KEY_ENV = "LINEAR_API_KEY"
DEFAULT_PAGE_SIZE = 50
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_BLOCKER_GATED_STATES = ("Todo",)

_ISSUE_FIELDS = """
  id identifier title description priority url branchName createdAt updatedAt
  state { name }
  assignee { id }
  team { key }
  project { slugId }
  labels { nodes { name } }
  inverseRelations(first: 50) { nodes { type issue { id identifier state { name } } } }
"""

ISSUES_BY_STATES_QUERY = """
query SymphonyIssuesByStates($projectSlug: String!, $states: [String!]!, $first: Int!, $after: String) {
  issues(
    filter: { project: { slugId: { eq: $projectSlug } }, state: { name: { in: $states } } }
    first: $first
    after: $after
    orderBy: createdAt
  ) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _ISSUE_FIELDS

ISSUES_BY_IDS_QUERY = """
query SymphonyIssuesByIds($projectSlug: String!, $ids: [ID!]!, $first: Int!) {
  issues(filter: { project: { slugId: { eq: $projectSlug } }, id: { in: $ids } }, first: $first) {
    nodes { %s }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _ISSUE_FIELDS

VIEWER_QUERY = "query SymphonyViewer { viewer { id } }"

LINEAR_GRAPHQL_TOOL = ToolSpec(
    name="linear_graphql",
    description=(
        "Execute a GraphQL query or mutation against the Linear API using Symphony's configured "
        "credentials. Use it to read the current issue, post comments, change workflow state, or "
        "attach links. The current issue's Linear id is available in the prompt as issue.id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "GraphQL document."},
            "variables": {"type": "object", "description": "GraphQL variables.",
                          "additionalProperties": True},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


class LinearTracker(TrackerAdapter):
    kind = "linear"
    default_active_states = ("Todo", "In Progress")
    default_terminal_states = ("Done", "Closed", "Canceled", "Cancelled", "Duplicate")

    def __init__(self, config: ServiceConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(config)
        p = config.tracker.provider
        self._transport = transport

        endpoint = p.get("endpoint", DEFAULT_ENDPOINT)
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise TrackerError("tracker.provider.endpoint must be an http(s) URL",
                               category="invalid_tracker_config")
        self.endpoint = endpoint

        raw_key = p.get("api_key", f"${DEFAULT_API_KEY_ENV}")
        self._secret_env = {DEFAULT_API_KEY_ENV}
        ref = env_reference(raw_key)
        if ref:
            self._secret_env.add(ref)
        api_key = resolve_env_value(raw_key)
        if not isinstance(api_key, str) or not api_key.strip():
            raise TrackerError(
                "Linear API key is missing (set tracker.provider.api_key or $LINEAR_API_KEY)",
                category="missing_tracker_secret")
        self._api_key = api_key.strip()

        slug = resolve_env_value(p.get("project_slug"))
        if not isinstance(slug, str) or not slug.strip():
            raise TrackerError("tracker.provider.project_slug is required",
                               category="invalid_tracker_config")
        self.project_slug = slug.strip()

        assignee = resolve_env_value(p.get("assignee"))
        if assignee is not None and (not isinstance(assignee, str) or not assignee.strip()):
            raise TrackerError("tracker.provider.assignee must be 'me' or a Linear user id",
                               category="invalid_tracker_config")
        self.assignee = assignee.strip() if isinstance(assignee, str) else None
        self._assignee_id: str | None = None if self.assignee in (None, "me") else self.assignee

        gated = p.get("blocker_gated_states", list(DEFAULT_BLOCKER_GATED_STATES))
        if not isinstance(gated, list) or not all(isinstance(s, str) for s in gated):
            raise TrackerError("tracker.provider.blocker_gated_states must be a list of strings",
                               category="invalid_tracker_config")
        self.blocker_gated_states = {normalize_state(s) for s in gated}

        page_size = p.get("page_size", DEFAULT_PAGE_SIZE)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 250:
            raise TrackerError("tracker.provider.page_size must be an integer in 1..250",
                               category="invalid_tracker_config")
        self.page_size = page_size

        timeout_ms = p.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise TrackerError("tracker.provider.timeout_ms must be a positive integer",
                               category="invalid_tracker_config")
        self.timeout_s = timeout_ms / 1000

    # -- transport ---------------------------------------------------------------------------
    async def graphql(self, query: str, variables: dict[str, Any] | None = None,
                      *, raise_on_errors: bool = True) -> dict[str, Any]:
        headers = {"Authorization": self._api_key, "Content-Type": "application/json"}
        body = {"query": query, "variables": variables or {}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as c:
                resp = await c.post(self.endpoint, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise TrackerError(f"Linear request failed: {exc.__class__.__name__}",
                               category="tracker_request", retryable=True) from exc
        if resp.status_code == 429:
            raise TrackerError("Linear rate limit exceeded", category="tracker_rate_limited",
                               retryable=True, status=429,
                               retry_after_ms=_retry_after_ms(resp.headers.get("retry-after")))
        if resp.status_code >= 400 and resp.status_code != 400:
            raise TrackerError(f"Linear returned HTTP {resp.status_code}",
                               category="tracker_status", status=resp.status_code,
                               retryable=resp.status_code >= 500)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise TrackerError("Linear returned a non-JSON body", category="tracker_response",
                               status=resp.status_code) from exc
        if not isinstance(payload, dict):
            raise TrackerError("Linear returned a non-object body", category="tracker_response")
        errors = payload.get("errors")
        if errors and raise_on_errors:
            codes = {((e or {}).get("extensions") or {}).get("code") for e in errors
                     if isinstance(e, dict)}
            msg = "; ".join(str((e or {}).get("message", e)) for e in errors)[:500]
            if "RATELIMITED" in codes:
                raise TrackerError(f"Linear rate limited: {msg}", category="tracker_rate_limited",
                                   retryable=True)
            raise TrackerError(f"Linear GraphQL error: {msg}", category="tracker_response",
                               status=resp.status_code)
        if resp.status_code == 400 and raise_on_errors:
            raise TrackerError("Linear returned HTTP 400", category="tracker_status", status=400)
        return payload

    # -- reads -------------------------------------------------------------------------------
    async def fetch_issues_by_states(self, state_names: Iterable[str]) -> list[Issue]:
        states = [s for s in state_names if isinstance(s, str) and s.strip()]
        if not states:
            return []
        await self._resolve_assignee()
        issues: list[Issue] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            payload = await self.graphql(ISSUES_BY_STATES_QUERY, {
                "projectSlug": self.project_slug, "states": states,
                "first": self.page_size, "after": after})
            nodes, page = _issues_connection(payload)
            for node in nodes:
                try:
                    issues.append(self._normalize(node))
                except MalformedRecord as exc:
                    log.warning("tracker_record_omitted", reason=exc,
                                issue_id=_get(node, "id"), issue_identifier=_get(node, "identifier"))
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise TrackerError("Linear pagination returned no usable endCursor",
                                   category="tracker_pagination")
            seen_cursors.add(cursor)
            after = cursor
        return issues

    async def fetch_issues_by_ids(self, issue_ids: Iterable[str]) -> list[Issue]:
        ids = list(dict.fromkeys(i for i in issue_ids if isinstance(i, str) and i))
        if not ids:
            return []
        await self._resolve_assignee()
        found: dict[str, Issue] = {}
        for start in range(0, len(ids), self.page_size):
            chunk = ids[start:start + self.page_size]
            payload = await self.graphql(ISSUES_BY_IDS_QUERY, {
                "projectSlug": self.project_slug, "ids": chunk, "first": len(chunk)})
            nodes, page = _issues_connection(payload)
            if page.get("hasNextPage"):
                raise TrackerError("Linear ID refresh unexpectedly paginated",
                                   category="tracker_pagination")
            for node in nodes:
                node_id = _get(node, "id")
                if node_id not in chunk:
                    continue
                try:
                    found[node_id] = self._normalize(node)
                except MalformedRecord as exc:
                    raise TrackerError(f"Linear returned a malformed issue {node_id}: {exc}",
                                       category="tracker_response") from exc
        return list(found.values())

    async def _resolve_assignee(self) -> None:
        if self.assignee == "me" and self._assignee_id is None:
            payload = await self.graphql(VIEWER_QUERY)
            viewer = (payload.get("data") or {}).get("viewer") or {}
            vid = viewer.get("id")
            if not isinstance(vid, str) or not vid:
                raise TrackerError("Linear viewer query returned no id",
                                   category="tracker_response")
            self._assignee_id = vid

    def _normalize(self, node: Any) -> Issue:
        if not isinstance(node, dict):
            raise MalformedRecord("issue node is not an object")
        state = _get(node, "state", "name")
        labels = [_get(n, "name") for n in (_get(node, "labels", "nodes") or [])]
        blockers = []
        for rel in _get(node, "inverseRelations", "nodes") or []:
            if _get(rel, "type") != "blocks":
                continue
            b = _get(rel, "issue") or {}
            blockers.append({"id": _get(b, "id"), "identifier": _get(b, "identifier"),
                             "state": _get(b, "state", "name")})
        assignee_id = _get(node, "assignee", "id")
        dispatchable = True
        if self._assignee_id is not None and assignee_id != self._assignee_id:
            dispatchable = False
        if normalize_state(state) in self.blocker_gated_states:
            terminal = {normalize_state(s) for s in self.terminal_states}
            if any(normalize_state(b["state"]) not in terminal for b in blockers):
                dispatchable = False
        raw = {
            "id": _get(node, "id"),
            "identifier": _get(node, "identifier"),
            "title": _get(node, "title"),
            "state": state,
            "description": _get(node, "description"),
            "priority": _get(node, "priority"),
            "branch_name": _get(node, "branchName"),
            "url": _get(node, "url"),
            "assignee_id": assignee_id,
            "labels": labels,
            "blocked_by": blockers,
            "created_at": _get(node, "createdAt"),
            "updated_at": _get(node, "updatedAt"),
            "native_ref": {
                "provider": "linear",
                "issue_id": _get(node, "id"),
                "team_key": _get(node, "team", "key"),
                "project_slug": _get(node, "project", "slugId"),
            },
        }
        return build_issue(raw, dispatchable=dispatchable)

    # -- agent tools -------------------------------------------------------------------------
    def agent_tool_specs(self) -> list[ToolSpec]:
        return [LINEAR_GRAPHQL_TOOL]

    def secret_environment_names(self) -> list[str]:
        return sorted(self._secret_env)

    async def execute_agent_tool(self, name: str, arguments: Any,
                                 context: dict[str, Any]) -> ToolResult:
        if name != LINEAR_GRAPHQL_TOOL.name:
            return ToolResult.fail("unsupported_tool", f"unsupported tool: {name}")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return ToolResult.fail("invalid_arguments", "arguments must be a JSON object")
        if not isinstance(arguments, dict):
            return ToolResult.fail("invalid_arguments", "arguments must be an object")
        query = arguments.get("query")
        variables = arguments.get("variables") or {}
        if not isinstance(query, str) or not query.strip():
            return ToolResult.fail("invalid_arguments", "'query' must be a non-empty string")
        if not isinstance(variables, dict):
            return ToolResult.fail("invalid_arguments", "'variables' must be an object")
        try:
            payload = await self.graphql(query, variables, raise_on_errors=False)
        except TrackerError as exc:
            return ToolResult.fail(exc.category, exc.message, retryable=exc.retryable)
        if payload.get("errors"):
            return ToolResult(False, {"errors": payload["errors"], "data": payload.get("data")})
        return ToolResult.ok({"data": payload.get("data")})


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _issues_connection(payload: dict) -> tuple[list, dict]:
    conn = _get(payload, "data", "issues")
    if not isinstance(conn, dict):
        raise TrackerError("Linear response missing data.issues", category="tracker_response")
    nodes = conn.get("nodes")
    page = conn.get("pageInfo") or {}
    if not isinstance(nodes, list) or not isinstance(page, dict):
        raise TrackerError("Linear response has malformed issues connection",
                           category="tracker_response")
    return nodes, page


def _retry_after_ms(value: str | None) -> int | None:
    try:
        return int(float(value) * 1000) if value else None
    except ValueError:
        return None
