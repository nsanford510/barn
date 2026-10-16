"""WORKFLOW.md loader and change detection (spec sections 5 and 6.2)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import WorkflowError

DEFAULT_WORKFLOW_FILENAME = "WORKFLOW.md"


@dataclass(frozen=True)
class WorkflowDefinition:
    config: dict[str, Any]
    prompt_template: str
    path: str = ""
    digest: str = field(default="", compare=False)


def resolve_workflow_path(explicit: str | os.PathLike | None = None,
                          cwd: str | os.PathLike | None = None) -> Path:
    """Explicit runtime path wins; otherwise ``WORKFLOW.md`` in the process cwd (spec 5.1)."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path(cwd) if cwd else Path.cwd()).resolve() / DEFAULT_WORKFLOW_FILENAME


def parse_workflow(text: str, path: str = "") -> WorkflowDefinition:
    lines = text.splitlines()
    config: Any = {}
    body_lines = lines
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            raise WorkflowError("front matter is not terminated by '---'",
                                category="workflow_parse_error", path=path)
        front = "\n".join(lines[1:end])
        body_lines = lines[end + 1:]
        try:
            config = yaml.safe_load(front) if front.strip() else {}
        except yaml.YAMLError as exc:
            raise WorkflowError(f"invalid YAML front matter: {exc}",
                                category="workflow_parse_error", path=path) from exc
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise WorkflowError("YAML front matter must decode to a map",
                                category="workflow_front_matter_not_a_map", path=path)
    prompt = "\n".join(body_lines).strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return WorkflowDefinition(config=config, prompt_template=prompt, path=path, digest=digest)


def load_workflow(path: str | os.PathLike) -> WorkflowDefinition:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkflowError(f"cannot read workflow file {p}: {exc}",
                            category="missing_workflow_file", path=str(p)) from exc
    return parse_workflow(text, path=str(p.resolve()))


class WorkflowWatcher:
    """Detects WORKFLOW.md changes by stat signature plus content digest.

    Polling-based so it works on every filesystem and also catches atomic
    rename-style saves that inotify-style watchers can miss.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._signature: tuple | None = self._stat()

    def _stat(self) -> tuple | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def changed(self) -> bool:
        sig = self._stat()
        if sig != self._signature:
            self._signature = sig
            return True
        return False
