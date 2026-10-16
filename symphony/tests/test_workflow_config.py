"""Spec 17.1: workflow and config parsing."""

import os
import tempfile
import unittest
from pathlib import Path

from helpers import TempDirMixin, make_issue, write_workflow

from symphony.config import ServiceConfig, validate_dispatch_config
from symphony.errors import ConfigError, TemplateError, TrackerError, WorkflowError
from symphony.prompt import DEFAULT_PROMPT, render_prompt
from symphony.workflow import (WorkflowWatcher, load_workflow, parse_workflow,
                               resolve_workflow_path)


class WorkflowLoaderTest(TempDirMixin, unittest.TestCase):
    def test_explicit_path_wins(self):
        d = self.make_tmp()
        self.assertEqual(resolve_workflow_path(os.path.join(d, "x.md")),
                         Path(d).resolve() / "x.md")

    def test_cwd_default(self):
        d = self.make_tmp()
        self.assertEqual(resolve_workflow_path(None, cwd=d), Path(d).resolve() / "WORKFLOW.md")

    def test_missing_file_typed_error(self):
        with self.assertRaises(WorkflowError) as cm:
            load_workflow(os.path.join(self.make_tmp(), "nope.md"))
        self.assertEqual(cm.exception.category, "missing_workflow_file")

    def test_invalid_yaml_typed_error(self):
        with self.assertRaises(WorkflowError) as cm:
            parse_workflow("---\ntracker: [unclosed\n---\nbody")
        self.assertEqual(cm.exception.category, "workflow_parse_error")

    def test_non_map_front_matter(self):
        with self.assertRaises(WorkflowError) as cm:
            parse_workflow("---\n- a\n- b\n---\nbody")
        self.assertEqual(cm.exception.category, "workflow_front_matter_not_a_map")

    def test_no_front_matter_is_all_prompt(self):
        wf = parse_workflow("  hello {{ issue.title }}  \n")
        self.assertEqual(wf.config, {})
        self.assertEqual(wf.prompt_template, "hello {{ issue.title }}")

    def test_split_and_trim(self):
        wf = parse_workflow("---\npolling:\n  interval_ms: 5\nfuture_key: 1\n---\n\n body \n\n")
        self.assertEqual(wf.config["polling"]["interval_ms"], 5)
        self.assertEqual(wf.prompt_template, "body")

    def test_watcher_detects_change(self):
        d = self.make_tmp()
        p = write_workflow(d, "tracker:\n  kind: memory")
        w = WorkflowWatcher(p)
        self.assertFalse(w.changed())
        with open(p, "a") as fh:
            fh.write("more\n")
        self.assertTrue(w.changed())
        self.assertFalse(w.changed())


class ConfigTest(TempDirMixin, unittest.TestCase):
    def cfg(self, front: str, environ=None) -> ServiceConfig:
        d = self.make_tmp()
        return ServiceConfig.from_workflow(load_workflow(write_workflow(d, front)), environ)

    def test_defaults(self):
        c = self.cfg("tracker:\n  kind: memory")
        self.assertEqual(c.poll_interval_ms, 30000)
        self.assertEqual(c.workspace_root,
                         os.path.join(tempfile.gettempdir(), "symphony_workspaces"))
        self.assertEqual(c.hooks.timeout_ms, 60000)
        self.assertEqual(c.agent.max_concurrent_agents, 10)
        self.assertEqual(c.agent.max_turns, 20)
        self.assertEqual(c.agent.max_retry_backoff_ms, 300000)
        self.assertEqual(c.codex.command, "codex app-server")
        self.assertEqual(c.codex.turn_timeout_ms, 3600000)
        self.assertEqual(c.codex.read_timeout_ms, 5000)
        self.assertEqual(c.codex.stall_timeout_ms, 300000)
        self.assertEqual(c.tracker.provider, {})
        self.assertEqual(c.tracker.required_labels, ())

    def test_provider_keys_preserved(self):
        c = self.cfg("tracker:\n  kind: memory\n  provider:\n    custom: 1\n    nested: {a: b}")
        self.assertEqual(c.tracker.provider, {"custom": 1, "nested": {"a": "b"}})

    def test_unsupported_kind(self):
        with self.assertRaises(TrackerError) as cm:
            validate_dispatch_config(self.cfg("tracker:\n  kind: jira-ish"))
        self.assertEqual(cm.exception.category, "unsupported_tracker_kind")

    def test_missing_kind(self):
        with self.assertRaises(ConfigError):
            validate_dispatch_config(self.cfg("polling:\n  interval_ms: 10"))

    def test_empty_codex_command_fails_validation(self):
        with self.assertRaises(ConfigError):
            validate_dispatch_config(self.cfg("tracker:\n  kind: memory\ncodex:\n  command: '  '"))

    def test_codex_command_preserved_verbatim(self):
        c = self.cfg("tracker:\n  kind: memory\ncodex:\n  command: \"FOO=$HOME codex app-server --x '~'\"")
        self.assertEqual(c.codex.command, "FOO=$HOME codex app-server --x '~'")

    def test_workspace_root_env_and_tilde_and_relative(self):
        c = self.cfg("tracker:\n  kind: memory\nworkspace:\n  root: $WSROOT",
                     environ={"WSROOT": "/srv/ws"})
        self.assertEqual(c.workspace_root, "/srv/ws")
        c = self.cfg("tracker:\n  kind: memory\nworkspace:\n  root: ~/ws")
        self.assertEqual(c.workspace_root, os.path.expanduser("~/ws"))
        c = self.cfg("tracker:\n  kind: memory\nworkspace:\n  root: ./ws")
        self.assertEqual(c.workspace_root, os.path.join(c.workflow_dir, "ws"))
        with self.assertRaises(ConfigError):
            self.cfg("tracker:\n  kind: memory\nworkspace:\n  root: $UNSET_X", environ={})

    def test_invalid_hook_timeout_and_max_turns(self):
        with self.assertRaises(ConfigError):
            self.cfg("tracker:\n  kind: memory\nhooks:\n  timeout_ms: 0")
        with self.assertRaises(ConfigError):
            self.cfg("tracker:\n  kind: memory\nhooks:\n  timeout_ms: soon")
        with self.assertRaises(ConfigError):
            self.cfg("tracker:\n  kind: memory\nagent:\n  max_turns: 0")

    def test_per_state_limits_normalized_and_invalid_ignored(self):
        c = self.cfg("""
            tracker:
              kind: memory
            agent:
              max_concurrent_agents_by_state:
                " In Progress ": 2
                Todo: 0
                Review: abc
                QA: -1
                Done: "3"
        """)
        self.assertEqual(c.agent.max_concurrent_agents_by_state, {"in progress": 2, "done": 3})
        self.assertEqual(c.max_for_state("IN PROGRESS"), 2)
        self.assertEqual(c.max_for_state("todo"), 10)

    def test_required_labels_normalized(self):
        c = self.cfg("tracker:\n  kind: memory\n  required_labels: [' Agent ', BOT]")
        self.assertEqual(c.tracker.required_labels, ("agent", "bot"))


class PromptTest(unittest.TestCase):
    def test_renders_issue_and_attempt(self):
        issue = make_issue(1, labels=("a", "b"))
        out = render_prompt("{{ issue.identifier }} {% for l in issue.labels %}[{{ l }}]{% endfor %}"
                            "{% if attempt %} retry {{ attempt }}{% endif %}", issue, None)
        self.assertEqual(out, "T-1 [a][b]")
        self.assertEqual(render_prompt("{{ attempt }}", issue, 3), "3")

    def test_unknown_variable_fails(self):
        with self.assertRaises(TemplateError) as cm:
            render_prompt("{{ issue.nope }}", make_issue(1), None)
        self.assertEqual(cm.exception.category, "template_render_error")

    def test_unknown_filter_fails(self):
        with self.assertRaises(TemplateError) as cm:
            render_prompt("{{ issue.title | shout }}", make_issue(1), None)
        self.assertEqual(cm.exception.category, "template_render_error")

    def test_parse_error(self):
        with self.assertRaises(TemplateError) as cm:
            render_prompt("{% if %}", make_issue(1), None)
        self.assertEqual(cm.exception.category, "template_parse_error")

    def test_empty_body_uses_default(self):
        self.assertEqual(render_prompt("   ", make_issue(1), None), DEFAULT_PROMPT)


if __name__ == "__main__":
    unittest.main()
