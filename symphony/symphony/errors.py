"""Typed error surface shared across Symphony layers.

Every public error carries a stable ``category`` (spec sections 5.5, 10.6, 11.4) and a
human-readable ``message``. The orchestrator only relies on success vs. failure.
"""

from __future__ import annotations


class SymphonyError(Exception):
    category = "symphony_error"

    def __init__(self, message: str, *, category: str | None = None, **detail):
        super().__init__(message)
        if category:
            self.category = category
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.category}: {self.message}"


class WorkflowError(SymphonyError):
    """missing_workflow_file | workflow_parse_error | workflow_front_matter_not_a_map"""

    category = "workflow_parse_error"


class ConfigError(SymphonyError):
    category = "invalid_config"


class TemplateError(SymphonyError):
    """template_parse_error | template_render_error"""

    category = "template_render_error"


class TrackerError(SymphonyError):
    """unsupported_tracker_kind | invalid_tracker_config | missing_tracker_secret |
    tracker_request | tracker_status | tracker_response | tracker_pagination |
    tracker_rate_limited"""

    category = "tracker_request"

    def __init__(self, message: str, *, category: str | None = None,
                 retryable: bool = False, retry_after_ms: int | None = None,
                 status: int | None = None, **detail):
        super().__init__(message, category=category, **detail)
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.status = status


class WorkspaceError(SymphonyError):
    """invalid_workspace_path | workspace_create_failed | hook_failed | hook_timeout"""

    category = "workspace_error"


class AgentError(SymphonyError):
    """codex_not_found | invalid_workspace_cwd | response_timeout | turn_timeout | port_exit |
    response_error | turn_failed | turn_cancelled | turn_input_required | startup_failed"""

    category = "response_error"
