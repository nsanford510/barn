"""Strict Liquid prompt rendering (spec sections 5.4 and 12)."""

from __future__ import annotations

from liquid import Environment, StrictUndefined
from liquid.exceptions import LiquidError, LiquidSyntaxError, UnknownFilterError

from .errors import TemplateError
from .models import Issue

DEFAULT_PROMPT = "You are working on an issue from the configured tracker."

_ENV = Environment(undefined=StrictUndefined)


def render_prompt(template: str, issue: Issue, attempt: int | None) -> str:
    source = template if template.strip() else DEFAULT_PROMPT
    try:
        compiled = _ENV.from_string(source)
    except UnknownFilterError as exc:
        raise TemplateError(str(exc), category="template_render_error") from exc
    except LiquidSyntaxError as exc:
        raise TemplateError(str(exc), category="template_parse_error") from exc
    except LiquidError as exc:
        raise TemplateError(str(exc), category="template_parse_error") from exc
    try:
        return compiled.render(issue=issue.to_template_dict(), attempt=attempt)
    except LiquidError as exc:
        raise TemplateError(str(exc), category="template_render_error") from exc


def continuation_prompt(issue: Issue, turn_number: int, max_turns: int) -> str:
    """Guidance for in-worker continuation turns; never resends the original task prompt."""
    return (
        f"Continue working on {issue.identifier}: {issue.title}. "
        f"The issue is still in tracker state '{issue.state}'. "
        f"This is continuation turn {turn_number} of at most {max_turns} in this session. "
        "Pick up where you left off, keep following the original instructions already in this "
        "thread, and move the issue to the workflow's handoff state when the work is complete."
    )
