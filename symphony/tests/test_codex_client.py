"""Spec 17.5: coding-agent app-server client, exercised against a scripted fake server."""

import json
import os
import shlex
import sys
import unittest

from helpers import FAKE_SERVER, TempDirMixin, make_issue

from symphony.codex import AppServerSession, ToolExecutor, extract_absolute_usage
from symphony.config import CodexConfig
from symphony.errors import AgentError
from symphony.models import ToolResult, ToolSpec


class CodexClientTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def session(self, mode, *, tools=None, read_timeout_ms=3000, turn_timeout_ms=5000,
                      secrets=(), auto_approve=True, **codex_kw):
        root = self.make_tmp()
        ws = os.path.join(root, "T-1")
        os.mkdir(ws)
        self.log_path = os.path.join(self.make_tmp(), "fake.log")
        cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_SERVER))} {mode} " \
              f"{shlex.quote(self.log_path)}"
        cfg = CodexConfig(command=cmd, read_timeout_ms=read_timeout_ms,
                          turn_timeout_ms=turn_timeout_ms, **codex_kw)
        self.events = []
        s = AppServerSession(cfg, workspace_path=os.path.realpath(ws), workspace_root=root,
                             on_event=self.events.append, tools=tools, secret_env_names=secrets,
                             auto_approve=auto_approve)
        self.addAsyncCleanup(s.stop)
        self.ws = os.path.realpath(ws)
        return s

    def log_lines(self):
        with open(self.log_path) as fh:
            return [json.loads(line) for line in fh]

    def sent(self, method):
        return [e["recv"] for e in self.log_lines() if "recv" in e and e["recv"].get("method") == method]

    def names(self):
        return [e["event"] for e in self.events]

    async def test_handshake_turn_and_telemetry(self):
        os.environ["SYMPHONY_TEST_SECRET"] = "hunter2"
        self.addCleanup(os.environ.pop, "SYMPHONY_TEST_SECRET", None)
        s = await self.session("ok", secrets=["SYMPHONY_TEST_SECRET"])
        thread = await s.start(make_issue(1, title="Fix it"))
        self.assertEqual(thread, "thr_1")
        result = await s.run_turn("do the thing", make_issue(1))
        self.assertEqual((result.thread_id, result.turn_id, result.status),
                         ("thr_1", "turn_1", "completed"))
        await s.run_turn("continue", make_issue(1))  # same live thread

        first = self.log_lines()[0]
        self.assertEqual(os.path.realpath(first["cwd"]), self.ws)
        self.assertIsNone(first["env_SYMPHONY_TEST_SECRET"])  # secret stripped from child env

        init = self.sent("initialize")[0]
        self.assertEqual(init["params"]["clientInfo"]["name"], "symphony")
        self.assertEqual(len(self.sent("initialized")), 1)
        ts = self.sent("thread/start")[0]["params"]
        self.assertEqual(ts["cwd"], self.ws)
        self.assertEqual(ts["approvalPolicy"], "never")
        self.assertEqual(ts["sandbox"], "workspace-write")
        self.assertEqual(self.sent("thread/name/set")[0]["params"]["name"], "T-1: Fix it")
        turns = self.sent("turn/start")
        self.assertEqual(len(turns), 2)
        self.assertEqual({t["params"]["threadId"] for t in turns}, {"thr_1"})
        self.assertEqual(turns[0]["params"]["input"][0]["text"], "do the thing")
        self.assertEqual(turns[0]["params"]["sandboxPolicy"],
                         {"type": "workspaceWrite", "writableRoots": [self.ws],
                          "networkAccess": False})

        started = [e for e in self.events if e["event"] == "session_started"]
        self.assertEqual([e["session_id"] for e in started], ["thr_1-turn_1", "thr_1-turn_2"])
        self.assertTrue(all(e["codex_app_server_pid"] for e in started))
        usages = [e["usage"] for e in self.events if e.get("usage")]
        self.assertEqual(usages[-1], {"input_tokens": 200, "output_tokens": 20,
                                      "total_tokens": 220})
        self.assertTrue(any(e.get("rate_limits") for e in self.events))
        self.assertIn("turn_completed", self.names())

    async def test_policy_fields_pass_through(self):
        s = await self.session("ok", approval_policy="on-request", thread_sandbox="read-only",
                               turn_sandbox_policy={"type": "workspaceWrite",
                                                    "writableRoots": ["$WORKSPACE", "/tmp/x"]})
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        self.assertEqual(self.sent("thread/start")[0]["params"]["sandbox"], "read-only")
        tp = self.sent("turn/start")[0]["params"]
        self.assertEqual(tp["approvalPolicy"], "on-request")
        self.assertEqual(tp["sandboxPolicy"]["writableRoots"], [self.ws, "/tmp/x"])

    async def test_dynamic_tools_advertised_and_executed(self):
        calls = []

        async def execute(name, args, ctx):
            calls.append((name, args, ctx["issue"].identifier))
            return ToolResult.ok({"done": True})
        tools = ToolExecutor([ToolSpec("file_tracker_update", "update", {"type": "object"})],
                             execute)
        s = await self.session("tool", tools=tools)
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        spec = self.sent("thread/start")[0]["params"]["dynamicTools"]
        self.assertEqual(spec[0]["name"], "file_tracker_update")
        self.assertEqual(calls, [("file_tracker_update", {"comment": "hello"}, "T-1")])
        resp = next(e["tool_response"] for e in self.log_lines() if "tool_response" in e)
        self.assertTrue(resp["result"]["success"])
        self.assertEqual(json.loads(resp["result"]["contentItems"][0]["text"]), {"done": True})

    async def test_unsupported_tool_call_fails_without_stalling(self):
        s = await self.session("unknown_tool")
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        resp = next(e["tool_response"] for e in self.log_lines() if "tool_response" in e)
        self.assertFalse(resp["result"]["success"])
        self.assertIn("unsupported_tool_call", self.names())

    async def test_approvals_auto_approved(self):
        s = await self.session("approval")
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        approvals = next(e["approvals"] for e in self.log_lines() if "approvals" in e)
        self.assertEqual([a["result"]["decision"] for a in approvals],
                         ["acceptForSession", "acceptForSession", "approved_for_session"])
        self.assertIn("approval_auto_approved", self.names())

    async def test_approvals_declined_when_auto_approve_off(self):
        s = await self.session("approval", auto_approve=False)
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        approvals = next(e["approvals"] for e in self.log_lines() if "approvals" in e)
        self.assertEqual([a["result"]["decision"] for a in approvals],
                         ["decline", "decline", "denied"])

    async def test_user_input_fails_turn(self):
        s = await self.session("input")
        await s.start(make_issue(1))
        with self.assertRaises(AgentError) as cm:
            await s.run_turn("p", make_issue(1))
        self.assertEqual(cm.exception.category, "turn_input_required")
        self.assertIn("turn_input_required", self.names())

    async def test_turn_failed_and_interrupted(self):
        for mode, category, event in (("fail", "turn_failed", "turn_failed"),
                                      ("interrupt", "turn_cancelled", "turn_cancelled")):
            s = await self.session(mode)
            await s.start(make_issue(1))
            with self.assertRaises(AgentError) as cm:
                await s.run_turn("p", make_issue(1))
            self.assertEqual(cm.exception.category, category)
            self.assertIn(event, self.names())
            await s.stop()

    async def test_turn_timeout(self):
        s = await self.session("hang", turn_timeout_ms=300)
        await s.start(make_issue(1))
        with self.assertRaises(AgentError) as cm:
            await s.run_turn("p", make_issue(1))
        self.assertEqual(cm.exception.category, "turn_timeout")

    async def test_read_timeout(self):
        s = await self.session("no_init_response", read_timeout_ms=300)
        with self.assertRaises(AgentError) as cm:
            await s.start(make_issue(1))
        self.assertEqual(cm.exception.category, "response_timeout")
        self.assertIn("startup_failed", self.names())

    async def test_process_exit(self):
        s = await self.session("exit_after_init")
        with self.assertRaises(AgentError) as cm:
            await s.start(make_issue(1))
        self.assertEqual(cm.exception.category, "port_exit")

    async def test_missing_executable(self):
        root = self.make_tmp()
        ws = os.path.join(root, "T-1")
        os.mkdir(ws)
        s = AppServerSession(CodexConfig(command="definitely-not-a-codex-binary app-server"),
                             workspace_path=ws, workspace_root=root, on_event=lambda e: None)
        with self.assertRaises(AgentError) as cm:
            await s.start(make_issue(1))
        self.assertEqual(cm.exception.category, "codex_not_found")

    async def test_stderr_and_non_json_stdout_do_not_break_protocol(self):
        s = await self.session("noisy")
        await s.start(make_issue(1))
        await s.run_turn("p", make_issue(1))
        self.assertIn("malformed", self.names())


class UsageExtractionTest(unittest.TestCase):
    def test_prefers_absolute_and_ignores_deltas(self):
        self.assertEqual(extract_absolute_usage("thread/tokenUsage/updated", {
            "tokenUsage": {"total": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7},
                           "last": {"inputTokens": 1}}}),
            {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
        self.assertEqual(extract_absolute_usage("codex/event/token_count", {
            "msg": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 9, "output_tokens": 1, "total_tokens": 10},
                "last_token_usage": {"input_tokens": 1}}}}),
            {"input_tokens": 9, "output_tokens": 1, "total_tokens": 10})
        self.assertIsNone(extract_absolute_usage("codex/event/token_count", {
            "msg": {"info": {"last_token_usage": {"input_tokens": 1}}}}))
        self.assertIsNone(extract_absolute_usage("item/completed", {"usage": {"input_tokens": 3}}))


if __name__ == "__main__":
    unittest.main()
