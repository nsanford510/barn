"""Workspace manager, lifecycle hooks, and safety invariants (spec section 9)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import signal
from pathlib import Path
from typing import Iterable

from .config import HooksConfig
from .errors import WorkspaceError
from .log import get_logger
from .models import Workspace

log = get_logger("workspace")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
HOOK_OUTPUT_LOG_LIMIT = 2_000


def workspace_key(identifier: str) -> str:
    """Sanitize to ``[A-Za-z0-9._-]``; append a 64-bit hash when sanitization changed anything."""
    sanitized = _UNSAFE.sub("_", identifier)
    if sanitized in ("", ".", ".."):
        sanitized = sanitized.replace(".", "_") or "_"
    if sanitized != identifier:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
        sanitized = f"{sanitized}-{digest}"
    return sanitized


def ensure_contained(root: str | Path, path: str | Path) -> Path:
    """Invariant 2: the (symlink-resolved) path must live strictly inside the root."""
    root_real = Path(os.path.realpath(root))
    path_real = Path(os.path.realpath(path))
    if path_real == root_real or root_real not in path_real.parents:
        raise WorkspaceError(f"workspace path {path_real} escapes workspace root {root_real}",
                             category="invalid_workspace_path")
    return path_real


def child_env(secret_names: Iterable[str] = ()) -> dict[str, str]:
    """Process environment minus tracker secrets (spec 10.5 / 15.3)."""
    env = dict(os.environ)
    for name in secret_names:
        env.pop(name, None)
    return env


def _truncate(text: str) -> str:
    text = text.strip()
    return text if len(text) <= HOOK_OUTPUT_LOG_LIMIT else text[-HOOK_OUTPUT_LOG_LIMIT:]


async def run_hook(name: str, script: str | None, cwd: str, timeout_ms: int, *,
                   env: dict[str, str] | None = None, context: dict | None = None) -> None:
    """Run a hook via ``bash -lc`` in ``cwd``. Raises WorkspaceError on failure or timeout."""
    if not script:
        return
    ctx = context or {}
    log.info("hook_started", hook=name, cwd=cwd, **ctx)
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-lc", script, cwd=cwd, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        log.error("hook_failed", hook=name, reason=exc, **ctx)
        raise WorkspaceError(f"hook {name} could not start: {exc}", category="hook_failed") from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_ms / 1000)
    except asyncio.TimeoutError:
        _kill_group(proc)
        await proc.wait()
        log.error("hook_timeout", hook=name, timeout_ms=timeout_ms, **ctx)
        raise WorkspaceError(f"hook {name} timed out after {timeout_ms}ms",
                             category="hook_timeout")
    except asyncio.CancelledError:
        _kill_group(proc)
        raise
    output = _truncate(out.decode("utf-8", "replace") if out else "")
    if proc.returncode != 0:
        log.error("hook_failed", hook=name, exit_code=proc.returncode, output=output, **ctx)
        raise WorkspaceError(f"hook {name} exited with status {proc.returncode}",
                             category="hook_failed", output=output)
    log.info("hook_completed", hook=name, **ctx)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class WorkspaceManager:
    def __init__(self, root: str, hooks: HooksConfig, secret_env_names: Iterable[str] = ()):
        self.root = os.path.abspath(root)
        self.hooks = hooks
        self.secret_env_names = tuple(secret_env_names)

    def path_for(self, identifier: str) -> str:
        path = os.path.join(self.root, workspace_key(identifier))
        if os.path.dirname(path) != self.root:
            raise WorkspaceError("workspace key produced a nested path",
                                 category="invalid_workspace_path")
        return path

    def _env(self) -> dict[str, str]:
        return child_env(self.secret_env_names)

    async def create_for_issue(self, identifier: str, context: dict | None = None) -> Workspace:
        key = workspace_key(identifier)
        path = self.path_for(identifier)
        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"cannot create workspace root {self.root}: {exc}",
                                 category="workspace_create_failed") from exc
        created_now = False
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isdir(path):
                # Policy: never replace a non-directory entry; fail the attempt instead.
                raise WorkspaceError(f"workspace path {path} exists and is not a directory",
                                     category="invalid_workspace_path")
        else:
            try:
                os.mkdir(path)
                created_now = True
            except FileExistsError:
                created_now = False
            except OSError as exc:
                raise WorkspaceError(f"cannot create workspace {path}: {exc}",
                                     category="workspace_create_failed") from exc
        real = ensure_contained(self.root, path)
        if created_now and self.hooks.after_create:
            try:
                await run_hook("after_create", self.hooks.after_create, str(real),
                               self.hooks.timeout_ms, env=self._env(), context=context)
            except BaseException:
                # Remove the partially prepared brand-new directory so the next attempt re-runs
                # after_create from scratch.
                shutil.rmtree(real, ignore_errors=True)
                raise
        return Workspace(path=str(real), workspace_key=key, created_now=created_now)

    async def before_run(self, path: str, context: dict | None = None) -> None:
        await run_hook("before_run", self.hooks.before_run, path, self.hooks.timeout_ms,
                       env=self._env(), context=context)

    async def after_run(self, path: str, context: dict | None = None) -> None:
        """Best effort: failures and timeouts are logged and ignored."""
        if not self.hooks.after_run or not os.path.isdir(path):
            return
        try:
            await run_hook("after_run", self.hooks.after_run, path, self.hooks.timeout_ms,
                           env=self._env(), context=context)
        except WorkspaceError:
            pass

    async def remove(self, identifier: str, context: dict | None = None) -> bool:
        path = self.path_for(identifier)
        if not os.path.lexists(path):
            return False
        if os.path.islink(path) or not os.path.isdir(path):
            log.warning("workspace_remove_skipped", path=path, reason="not a directory",
                        **(context or {}))
            return False
        real = ensure_contained(self.root, path)
        if self.hooks.before_remove:
            try:
                await run_hook("before_remove", self.hooks.before_remove, str(real),
                               self.hooks.timeout_ms, env=self._env(), context=context)
            except WorkspaceError:
                pass
        shutil.rmtree(real, ignore_errors=True)
        log.info("workspace_removed", path=str(real), **(context or {}))
        return True
