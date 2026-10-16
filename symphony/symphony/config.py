"""Typed configuration layer (spec section 6).

``ServiceConfig.from_workflow`` applies defaults, resolves ``$VAR`` indirection for path values,
coerces types, and raises ``ConfigError`` on invalid values. Tracker ``provider`` maps are
preserved verbatim; the selected adapter validates them.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .models import normalize_label, normalize_state
from .workflow import WorkflowDefinition

DEFAULT_POLL_INTERVAL_MS = 30_000
DEFAULT_HOOK_TIMEOUT_MS = 60_000
DEFAULT_MAX_CONCURRENT_AGENTS = 10
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_RETRY_BACKOFF_MS = 300_000
DEFAULT_CODEX_COMMAND = "codex app-server"
DEFAULT_TURN_TIMEOUT_MS = 3_600_000
DEFAULT_READ_TIMEOUT_MS = 5_000
DEFAULT_STALL_TIMEOUT_MS = 300_000

# Implementation-defined Codex policy defaults (documented in README "Trust posture").
DEFAULT_APPROVAL_POLICY = "never"
DEFAULT_THREAD_SANDBOX = "workspace-write"
WORKSPACE_PLACEHOLDER = "$WORKSPACE"

_ENV_REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


def env_reference(value: Any) -> str | None:
    """Return VAR for a value of the exact form ``$VAR``, else None."""
    if isinstance(value, str):
        m = _ENV_REF.match(value.strip())
        if m:
            return m.group(1)
    return None


def resolve_env_value(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Resolve ``$VAR`` to its environment value; empty/unset resolves to ''."""
    name = env_reference(value)
    if name is None:
        return value
    env = os.environ if environ is None else environ
    return env.get(name, "")


def _section(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{key}' must be a map", category="invalid_config")
    return value


def _int(section: dict, key: str, default: int, label: str, *, minimum: int | None = None) -> int:
    value = section.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer", category="invalid_config")
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise ConfigError(f"{label} must be an integer", category="invalid_config")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{label} must be >= {minimum}", category="invalid_config")
    return value


def _str_list(section: dict, key: str, label: str) -> list[str] | None:
    if key not in section or section[key] is None:
        return None
    value = section[key]
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
        raise ConfigError(f"{label} must be a list of strings", category="invalid_config")
    return [str(v) for v in value]


def _opt_script(section: dict, key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"hooks.{key} must be a shell script string", category="invalid_config")
    return value if value.strip() else None


def resolve_path(value: str, base_dir: Path, environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    name = env_reference(value)
    if name is not None:
        resolved = env.get(name, "")
        if not resolved:
            raise ConfigError(f"path references unset environment variable ${name}",
                              category="invalid_config")
        value = resolved
    value = os.path.expanduser(value)
    # Expand embedded $VAR / ${VAR} segments for path values only.
    value = os.path.expandvars(value) if environ is None else _expand_with(value, env)
    p = Path(value)
    if not p.is_absolute():
        p = base_dir / p
    return Path(os.path.normpath(str(p.absolute())))


def _expand_with(value: str, env: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return env.get(name, m.group(0))
    return re.sub(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))", repl, value)


@dataclass(frozen=True)
class TrackerConfig:
    kind: str | None
    provider: dict[str, Any]
    required_labels: tuple[str, ...]
    active_states: tuple[str, ...] | None  # None -> adapter default
    terminal_states: tuple[str, ...] | None


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS


@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS
    max_turns: int = DEFAULT_MAX_TURNS
    max_retry_backoff_ms: int = DEFAULT_MAX_RETRY_BACKOFF_MS
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexConfig:
    command: str = DEFAULT_CODEX_COMMAND
    approval_policy: Any = DEFAULT_APPROVAL_POLICY
    thread_sandbox: Any = DEFAULT_THREAD_SANDBOX
    turn_sandbox_policy: Any = None  # None -> workspaceWrite rooted at the workspace
    turn_timeout_ms: int = DEFAULT_TURN_TIMEOUT_MS
    read_timeout_ms: int = DEFAULT_READ_TIMEOUT_MS
    stall_timeout_ms: int = DEFAULT_STALL_TIMEOUT_MS
    model: str | None = None
    auto_approve: bool = True

    def turn_sandbox_for(self, workspace: str) -> Any:
        if self.turn_sandbox_policy is None:
            return {"type": "workspaceWrite", "writableRoots": [workspace],
                    "networkAccess": False}
        return _replace_placeholder(self.turn_sandbox_policy, workspace)


def _replace_placeholder(value: Any, workspace: str) -> Any:
    if isinstance(value, str):
        return value.replace(WORKSPACE_PLACEHOLDER, workspace)
    if isinstance(value, list):
        return [_replace_placeholder(v, workspace) for v in value]
    if isinstance(value, dict):
        return {k: _replace_placeholder(v, workspace) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class ServerConfig:
    port: int | None = None
    host: str = "127.0.0.1"


@dataclass(frozen=True)
class ServiceConfig:
    workflow_path: str
    workflow_dir: str
    tracker: TrackerConfig
    poll_interval_ms: int
    workspace_root: str
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    server: ServerConfig

    def max_for_state(self, state: str) -> int:
        return self.agent.max_concurrent_agents_by_state.get(
            normalize_state(state), self.agent.max_concurrent_agents)

    @classmethod
    def from_workflow(cls, wf: WorkflowDefinition,
                      environ: dict[str, str] | None = None) -> "ServiceConfig":
        raw = wf.config
        workflow_path = Path(wf.path) if wf.path else Path.cwd() / "WORKFLOW.md"
        base_dir = workflow_path.parent

        # tracker
        t = _section(raw, "tracker")
        kind = t.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise ConfigError("tracker.kind must be a string", category="invalid_config")
        provider = t.get("provider") or {}
        if not isinstance(provider, dict):
            raise ConfigError("tracker.provider must be a map", category="invalid_tracker_config")
        labels = _str_list(t, "required_labels", "tracker.required_labels") or []
        active = _str_list(t, "active_states", "tracker.active_states")
        terminal = _str_list(t, "terminal_states", "tracker.terminal_states")
        tracker = TrackerConfig(
            kind=kind.strip() if isinstance(kind, str) else None,
            provider=dict(provider),
            # A blank configured label is kept as "" so it matches no issue (spec 5.3.1).
            required_labels=tuple(normalize_label(lbl) for lbl in labels),
            active_states=tuple(active) if active is not None else None,
            terminal_states=tuple(terminal) if terminal is not None else None,
        )

        polling = _section(raw, "polling")
        poll_interval_ms = _int(polling, "interval_ms", DEFAULT_POLL_INTERVAL_MS,
                                "polling.interval_ms", minimum=1)

        ws = _section(raw, "workspace")
        root_raw = ws.get("root")
        if root_raw is None:
            root = Path(tempfile.gettempdir()) / "symphony_workspaces"
        elif isinstance(root_raw, str) and root_raw.strip():
            root = resolve_path(root_raw.strip(), base_dir, environ)
        else:
            raise ConfigError("workspace.root must be a non-empty path string",
                              category="invalid_config")

        h = _section(raw, "hooks")
        hooks = HooksConfig(
            after_create=_opt_script(h, "after_create"),
            before_run=_opt_script(h, "before_run"),
            after_run=_opt_script(h, "after_run"),
            before_remove=_opt_script(h, "before_remove"),
            timeout_ms=_int(h, "timeout_ms", DEFAULT_HOOK_TIMEOUT_MS, "hooks.timeout_ms",
                            minimum=1),
        )

        a = _section(raw, "agent")
        by_state_raw = a.get("max_concurrent_agents_by_state") or {}
        by_state: dict[str, int] = {}
        if isinstance(by_state_raw, dict):
            for state, limit in by_state_raw.items():
                if isinstance(limit, bool):
                    continue
                if isinstance(limit, str) and limit.strip().isdigit():
                    limit = int(limit.strip())
                if isinstance(limit, int) and limit > 0 and normalize_state(str(state)):
                    by_state[normalize_state(str(state))] = limit
        agent = AgentConfig(
            max_concurrent_agents=_int(a, "max_concurrent_agents", DEFAULT_MAX_CONCURRENT_AGENTS,
                                       "agent.max_concurrent_agents", minimum=0),
            max_turns=_int(a, "max_turns", DEFAULT_MAX_TURNS, "agent.max_turns", minimum=1),
            max_retry_backoff_ms=_int(a, "max_retry_backoff_ms", DEFAULT_MAX_RETRY_BACKOFF_MS,
                                      "agent.max_retry_backoff_ms", minimum=0),
            max_concurrent_agents_by_state=by_state,
        )

        c = _section(raw, "codex")
        command = c.get("command", DEFAULT_CODEX_COMMAND)
        if command is None:
            command = DEFAULT_CODEX_COMMAND
        if not isinstance(command, str):
            raise ConfigError("codex.command must be a shell command string",
                              category="invalid_config")
        model = c.get("model")
        auto_approve = c.get("auto_approve", True)
        if not isinstance(auto_approve, bool):
            raise ConfigError("codex.auto_approve must be a boolean", category="invalid_config")
        codex = CodexConfig(
            command=command,  # preserved verbatim; launched via `bash -lc`
            approval_policy=c.get("approval_policy", DEFAULT_APPROVAL_POLICY),
            thread_sandbox=c.get("thread_sandbox", DEFAULT_THREAD_SANDBOX),
            turn_sandbox_policy=c.get("turn_sandbox_policy"),
            turn_timeout_ms=_int(c, "turn_timeout_ms", DEFAULT_TURN_TIMEOUT_MS,
                                 "codex.turn_timeout_ms", minimum=1),
            read_timeout_ms=_int(c, "read_timeout_ms", DEFAULT_READ_TIMEOUT_MS,
                                 "codex.read_timeout_ms", minimum=1),
            stall_timeout_ms=_int(c, "stall_timeout_ms", DEFAULT_STALL_TIMEOUT_MS,
                                  "codex.stall_timeout_ms"),
            model=model if isinstance(model, str) and model.strip() else None,
            auto_approve=auto_approve,
        )

        s = _section(raw, "server")
        port = None
        if "port" in s and s["port"] is not None:
            port = _int(s, "port", 0, "server.port", minimum=0)
        host = s.get("host", "127.0.0.1")
        server = ServerConfig(port=port, host=str(host) if host else "127.0.0.1")

        return cls(
            workflow_path=str(workflow_path),
            workflow_dir=str(base_dir),
            tracker=tracker,
            poll_interval_ms=poll_interval_ms,
            workspace_root=str(root),
            hooks=hooks,
            agent=agent,
            codex=codex,
            server=server,
        )


def validate_dispatch_config(config: ServiceConfig) -> None:
    """Dispatch preflight (spec 6.3). Tracker-specific checks happen in the adapter factory."""
    from .trackers import build_tracker  # local import to avoid a cycle

    if not config.tracker.kind:
        raise ConfigError("tracker.kind is required", category="unsupported_tracker_kind")
    build_tracker(config)  # raises TrackerError on unsupported kind / invalid provider config
    if not config.codex.command.strip():
        raise ConfigError("codex.command must be non-empty", category="invalid_config")
