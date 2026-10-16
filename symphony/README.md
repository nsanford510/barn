# Symphony

A Python implementation of the [Symphony Service Specification](https://github.com/openai/symphony/blob/main/SPEC.md)
(Draft v1). Symphony is a long-running daemon. It polls an issue tracker, creates an isolated
workspace for each eligible issue, and runs a Codex app-server session there until the issue
reaches a handoff or terminal state.

It targets **core conformance** (spec 18.1) plus two optional extensions: the HTTP
dashboard/API (13.7) and provider-native agent tools (10.5). The SSH worker extension
(Appendix A) is not implemented.

## Quick start

```bash
cd scan && source .venv/bin/activate          # shared repo venv
pip install -r ../symphony/requirements.txt    # PyYAML, python-liquid, httpx
cd ../symphony
cp WORKFLOW.example.md /path/to/repo/WORKFLOW.md   # edit tracker + hooks
export LINEAR_API_KEY=lin_api_...
python -m symphony /path/to/repo/WORKFLOW.md --port 4100
```

- `python -m symphony [path-to-WORKFLOW.md]`: the default is `./WORKFLOW.md`.
- `--port N`: enables the dashboard at `/` and the JSON API at `/api/v1/*`. It overrides
  `server.port`, and `0` picks an ephemeral port. The server binds to `127.0.0.1` unless you
  pass `--host`.
- `--log-level`, `--log-file`: logs go to stderr and, optionally, to a file.
- Exit codes: `0` for a normal shutdown (SIGINT/SIGTERM); `1` for a startup failure or an
  abnormal host exit.

Run the tests with `python -m unittest discover -s tests` from `symphony/`.

## Layout

| Module              | Spec     | Role                                                                                   |
| ------------------- | -------- | -------------------------------------------------------------------------------------- |
| `workflow.py`     | 5, 6.2   | `WORKFLOW.md` loading (YAML front matter + prompt body) and change detection         |
| `config.py`       | 5.3, 6   | Typed config with defaults, `$VAR` and `~` path resolution, and dispatch preflight |
| `prompt.py`       | 5.4, 12  | Strict Liquid rendering (unknown variables or filters fail) and continuation prompts   |
| `workspace.py`    | 9        | Workspace keys, root containment, lifecycle hooks                                      |
| `trackers/`       | 11       | Adapter contract, registry, the `linear` and `file` adapters                       |
| `codex.py`        | 10       | Codex app-server stdio client                                                          |
| `orchestrator.py` | 7, 8, 16 | Single-authority scheduler: dispatch, reconciliation, retries, telemetry               |
| `server.py`       | 13.7     | Optional HTTP dashboard and API                                                        |
| `cli.py`          | 17.7     | Host lifecycle                                                                         |

## Implementation-defined behavior

### Trust posture (spec 10.5, 15)

The defaults assume a **trusted environment**: you control the tracker scope and the repository.

- Approval policy `never` and thread sandbox `workspace-write`.
- Turn sandbox `{type: workspaceWrite, writableRoots: [<workspace>], networkAccess: false}`.
  Override it with `codex.turn_sandbox_policy`; the `$WORKSPACE` placeholder expands to the
  per-issue workspace path. Workflows that push to git need `networkAccess: true`.
- **Approvals:** any approval request that reaches Symphony (command execution, file change,
  or the legacy `execCommandApproval`/`applyPatchApproval`) is accepted for the session. Set
  `codex.auto_approve: false` to decline them instead. The agent keeps going without the action.
  Symphony has no operator approval channel.
- **Extra permission requests** (`item/permissions/requestApproval`) get no additional
  permissions. The configured sandbox is never widened at runtime.
- **User input** (`item/tool/requestUserInput`) fails the turn immediately with
  `turn_input_required`, and the worker is retried with backoff. MCP elicitations are
  declined.
- **Unsupported server requests** get a JSON-RPC `-32601` error. Unsupported dynamic tool calls
  get `{success: false}`. Neither stalls the session.
- **Tracker secrets** stay in the Symphony process. Every adapter declares its secret env
  names, and those are removed from the environment of the Codex child process and of hooks.
  Tool calls run host-side, so the agent only ever sees results. Don't put literal API keys in
  `WORKFLOW.md`: the agent can read the repository.
- Hooks are fully trusted shell scripts. They run as `bash -lc` in the workspace with
  `hooks.timeout_ms`, and their output is truncated to 2 KB in logs.

Possible hardening: run under a dedicated OS user or container, tighten the approval/sandbox
values, use `tracker.required_labels` and `provider.assignee` to restrict which issues reach
the agent, and use a Linear key scoped to a single workspace.

### Other choices

- **Workspace keys:** characters outside `[A-Za-z0-9._-]` become `_`. If that changes the
  identifier, the key gets `-<first 16 hex chars of sha256(identifier)>` (64 bits) appended.
- **Workspace conflicts:** if a non-directory or a symlink already exists at the workspace
  path, the attempt fails and nothing is replaced. If `after_create` fails, the brand-new
  directory is removed so the next attempt starts clean. Reused workspaces are never reset.
- **Codex extras:** `codex.model` (optional) is passed to `thread/start`. `codex.auto_approve`
  defaults to `true`.
- **Reload:** `WORKFLOW.md` is checked every second, and before every tick, by stat signature.
  A valid change applies to future ticks, retries, hooks, and sessions; in-flight sessions keep
  their snapshot. An invalid file keeps the last good config running for reconciliation but
  **pauses new dispatch** until the file is fixed. The error appears in the logs and in
  `/api/v1/state` under `health`. A changed `server.port` needs a restart.
- **Retries:** normal exits get a 1 s continuation retry. Failures wait
  `min(10s * 2^(attempt-1), agent.max_retry_backoff_ms)`. A retry that finds no free slot is
  requeued as `attempt+1` with `no available orchestrator slots`.
- **Template variables:** `issue` (every normalized field, with timestamps as RFC 3339
  strings) and `attempt`. There is no `retry_kind`. Continuation turns within one session send
  fixed guidance instead of re-sending the prompt.
- **Logs:** `key=value` with `issue_id`, `issue_identifier` and `session_id` where they apply.
  If a file sink fails, a warning goes to the remaining sink and the service keeps running.

## Codex app-server protocol

Verified against the schema generated by `codex app-server generate-json-schema --experimental`
(codex-cli 0.162.1), and against a live handshake with that binary.

- **Framing:** newline-delimited JSON-RPC over stdio with no `jsonrpc` field. Max line size is
  10 MB. stderr is logged separately at debug level.
- **Startup:** `initialize` (`clientInfo`, `capabilities.experimentalApi: true` so
  `dynamicTools` is accepted) → `initialized` → `thread/start` (`cwd`, `approvalPolicy`,
  `sandbox`, `dynamicTools`) → best-effort `thread/name/set` with `"<identifier>: <title>"`.
- **Turns:** `turn/start` (`threadId`, text `input`, `cwd`, `approvalPolicy`,
  `sandboxPolicy`), completed by `turn/completed` with `status` set to `completed`,
  `interrupted` (cancelled) or `failed`.
- **Session id:** `<thread.id>-<turn.id>`, and `session_started` is emitted once per turn.
- **Telemetry:** absolute token totals come from `thread/tokenUsage/updated.tokenUsage.total`
  (or legacy `total_token_usage`); `last` / delta payloads are ignored. Rate limits come from
  `account/rateLimits/updated`.
- **Timeouts:** `read_timeout_ms` applies to each request. `turn_timeout_ms` is a silence
  timeout that every message resets. The orchestrator enforces `stall_timeout_ms`.

## Tracker adapter profiles (spec 11.2)

### `linear`

| `tracker.provider` key | Default                                                                                                            | Notes                                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `api_key`              | `$LINEAR_API_KEY` | **Secret.** Use a `$VAR` reference. An empty value means `missing_tracker_secret`. |                                                                                                                  |
| `project_slug`         | (required)                                                                                                         | Linear project `slugId`; it defines the scope. Accepts `$VAR`.                                               |
| `endpoint`             | `https://api.linear.app/graphql`                                                                                 | Must be http(s).                                                                                                 |
| `assignee`             | unset                                                                                                              | `me` (resolved with a `viewer` query) or a user id. Non-matching issues get `dispatchable=false`.          |
| `blocker_gated_states` | `[Todo]`                                                                                                         | In these states, an issue with any `blocks` relation whose blocker isn't terminal gets `dispatchable=false`. |
| `page_size`            | `50`                                                                                                             | 1–250. Also the chunk size for ID refresh.                                                                      |
| `timeout_ms`           | `30000`                                                                                                          | HTTP timeout.                                                                                                    |

- **Defaults:** active states `Todo, In Progress`; terminal states
  `Done, Closed, Canceled, Cancelled, Duplicate`.
- **Secret env names** (stripped from children): `LINEAR_API_KEY`, plus any variable
  referenced by `api_key`.
- **Scope and pagination:** `issues(filter: {project: {slugId}, state: {name: {in}}})`,
  cursor-paginated and ordered by `createdAt`. A missing or repeated `endCursor` raises
  `tracker_pagination`. ID refresh uses `id: {in}` within the same project scope.
- **Mapping:** `id` is the Linear issue id. `native_ref = {provider, issue_id, team_key, project_slug}`. `priority` is Linear's integer (0 = none, which sorts after 1–4). `labels`
  are trimmed, lowercased and de-duplicated. `blocked_by` comes from `inverseRelations` of type
  `blocks`. Unparseable timestamps become `null`.
- **Malformed records** (missing `id`/`identifier`/`title`/`state`) are logged and skipped in
  state-list reads, but make an ID refresh fail with `tracker_response`.
- **Errors** (`TrackerError.category`): transport failure → `tracker_request`; HTTP 429 or
  GraphQL `RATELIMITED` → `tracker_rate_limited` (with `retry_after_ms`); any other non-2xx →
  `tracker_status`; non-JSON, GraphQL `errors`, or a malformed shape → `tracker_response`;
  pagination problems → `tracker_pagination`; bad config → `invalid_tracker_config` /
  `missing_tracker_secret`.
- **Tool `linear_graphql`** `{query: string, variables?: object}`: runs any query or
  **mutation** with the configured key, so its scope is whatever that key can reach. It returns
  `{data}` on success; on failure it returns `{errors, data}` or
  `{error: {category, message, retryable}}`. There is no client-side idempotency; Linear's rate
  limits apply.

### `file` (local, credential-free)

- **Config:** `tracker.provider.path` (required). It is resolved relative to `WORKFLOW.md` and
  accepts `$VAR` and `~`.
- **Contents:** a YAML or JSON list of issues, or `{issues: [...]}`. The file is re-read on
  every call, so editing it changes tracker state. `id` defaults to `identifier`.
- **Dispatchability:** `dispatchable` defaults to `true`, and becomes `false` when any
  `blocked_by` entry has a non-terminal state.
- **Defaults:** active states `Todo, In Progress`; terminal states
  `Done, Canceled, Cancelled`. No secrets. `native_ref = {provider: file, path}`.
- **Errors:** unreadable file → `tracker_request`; invalid YAML or shape → `tracker_response`.
  Malformed entries are omitted from state lists and fail ID refresh.
- **Tool `file_tracker_update`** `{state?: string, comment?: string}`: mutates **only** the
  current session's issue. It sets the state and/or appends to its `comments` list, writing
  the file atomically.

### Adding an adapter

Subclass `trackers.base.TrackerAdapter` and implement `fetch_issues_by_states` and
`fetch_issues_by_ids`. Optionally implement `agent_tool_specs`, `secret_environment_names` and
`execute_agent_tool`. Register it with `trackers.register_tracker(kind, cls)` and document its
profile here.

## HTTP API (extension)

- `GET /`: auto-refreshing HTML dashboard.
- `GET /api/v1/state`: running rows (including `turn_count`), retry rows, `codex_totals`
  (with live `seconds_running`), `rate_limits`, and `health` (workflow/dispatch errors, last
  tick).
- `GET /api/v1/<identifier>`: per-issue debug details. Unknown issues return `404 issue_not_found`.
- `POST /api/v1/refresh`: queues an immediate poll and reconcile, returning `202`. Repeated
  requests are coalesced.
- Wrong methods return `405`. Errors use `{"error": {"code", "message"}}`.

## Conformance notes

- Covered by tests (`tests/`): the spec 17.1–17.7 matrix, the HTTP extension, provider tools,
  and an end-to-end run (file tracker + scripted app-server subprocess + the real orchestrator
  loop).
- Real integration (17.8): a manual handshake against the installed `codex app-server` passed.
  No live Linear smoke test is automated; run one with a real `LINEAR_API_KEY` and a throwaway
  project before production use.
- **Not implemented:** the SSH worker extension, persisting retry state across restarts (a
  spec TODO), and per-session Codex log files (`logs.codex_session_logs` is always empty).
