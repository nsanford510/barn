"""Tracker adapter registry."""

from __future__ import annotations

from typing import Callable

from ..config import ServiceConfig
from ..errors import TrackerError
from .base import TrackerAdapter, TrackerPolicy

_REGISTRY: dict[str, Callable[[ServiceConfig], TrackerAdapter]] = {}


def register_tracker(kind: str, factory: Callable[[ServiceConfig], TrackerAdapter]) -> None:
    _REGISTRY[kind] = factory


def supported_kinds() -> list[str]:
    return sorted(_REGISTRY)


def build_tracker(config: ServiceConfig) -> TrackerAdapter:
    kind = config.tracker.kind
    factory = _REGISTRY.get(kind or "")
    if factory is None:
        raise TrackerError(
            f"unsupported tracker.kind {kind!r}; supported: {', '.join(supported_kinds())}",
            category="unsupported_tracker_kind")
    return factory(config)


def _register_builtin() -> None:
    from .file import FileTracker
    from .linear import LinearTracker

    register_tracker(LinearTracker.kind, LinearTracker)
    register_tracker(FileTracker.kind, FileTracker)


_register_builtin()

__all__ = ["TrackerAdapter", "TrackerPolicy", "build_tracker", "register_tracker",
           "supported_kinds"]
