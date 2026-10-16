"""Spec 17.6/17.7 + HTTP extension: observability, CLI lifecycle, and an end-to-end run."""

import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

import yaml
from helpers import FAKE_SERVER, FakeSession, MemoryTracker, TempDirMixin, make_issue, \
    write_workflow

from symphony.log import configure_logging, get_logger, kv
from symphony.orchestrator import Orchestrator
from symphony.server import StatusServer

ROOT = Path(__file__).resolve().parents[1]


async def http(port, method, path):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split()[1])
    return status, body


class HttpApiTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        d = self.make_tmp()
        path = write_workflow(d, "tracker:\n  kind: memory\nworkspace:\n  root: ./ws")
        MemoryTracker.reset([make_issue(1, url="https://t/1")])

        async def hang(*a):
            await asyncio.Event().wait()
        FakeSession.reset(hang)
        self.orch = Orchestrator(path, session_factory=FakeSession)
        await self.orch.initialize()
        await self.orch._handle(("tick",))
        await asyncio.sleep(0.02)
        self.server = StatusServer(self.orch, port=0)
        self.port = await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()
        await self.orch.stop()

    async def test_endpoints(self):
        status, body = await http(self.port, "GET", "/api/v1/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["counts"]["running"], 1)
        self.assertEqual(state["running"][0]["issue_identifier"], "T-1")
        self.assertIn("codex_totals", state)
        self.assertIn("rate_limits", state)

        status, body = await http(self.port, "GET", "/api/v1/T-1")
        self.assertEqual((status, json.loads(body)["status"]), (200, "running"))

        status, body = await http(self.port, "GET", "/api/v1/NOPE-9")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "issue_not_found")

        status, body = await http(self.port, "POST", "/api/v1/refresh")
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["queued"])
        status, body = await http(self.port, "POST", "/api/v1/refresh")
        self.assertTrue(json.loads(body)["coalesced"])

        for method, path in (("POST", "/api/v1/state"), ("GET", "/api/v1/refresh"),
                             ("DELETE", "/"), ("POST", "/api/v1/T-1")):
            status, body = await http(self.port, method, path)
            self.assertEqual(status, 405, (method, path))
            self.assertEqual(json.loads(body)["error"]["code"], "method_not_allowed")

        status, body = await http(self.port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"T-1", body)
        self.assertIn(b"Symphony", body)

    async def test_binds_loopback_by_default(self):
        self.assertEqual(self.server._server.sockets[0].getsockname()[0], "127.0.0.1")


class LoggingTest(TempDirMixin, unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger("symphony")
        root.handlers = [logging.NullHandler()]
        root.propagate = False

    def test_kv_format(self):
        self.assertEqual(kv(issue_id="a", issue_identifier="T-1", session_id=None, reason="x y"),
                         'issue_id=a issue_identifier=T-1 session_id=null reason="x y"')
        # Control characters (e.g. git's \r progress output) must not break the line.
        self.assertEqual(kv(output="Cloning\r\nfatal: no\x1b[0m"),
                         'output="Cloning\\r\\nfatal: no\\x1b[0m"')

    def test_issue_context_fields(self):
        with self.assertLogs("symphony.orchestrator", "INFO") as cm:
            get_logger("orchestrator").info("session_started", issue_id="a",
                                            issue_identifier="T-1", session_id="th-tu")
        self.assertIn("session_started issue_id=a issue_identifier=T-1 session_id=th-tu",
                      cm.output[0])

    def test_failing_sink_does_not_crash(self):
        log_dir = self.make_tmp()
        path = os.path.join(log_dir, "s.log")
        configure_logging("INFO", path)
        os.chmod(log_dir, 0o500)
        self.addCleanup(os.chmod, log_dir, 0o700)
        handler = logging.getLogger("symphony").handlers[1]
        handler.stream.close()  # simulate a sink failure mid-run
        get_logger("t").info("still_running")  # must not raise
        configure_logging("INFO", "/nonexistent-dir/x.log")  # unopenable sink: warn, continue


class CliTest(TempDirMixin, unittest.TestCase):
    def run_cli(self, *args, cwd=None, timeout=20):
        return subprocess.run([sys.executable, "-m", "symphony", *args], cwd=cwd or ROOT,
                              capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})

    def test_missing_explicit_and_default_workflow(self):
        r = self.run_cli(os.path.join(self.make_tmp(), "nope.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("workflow file not found", r.stderr)
        r = self.run_cli(cwd=self.make_tmp())
        self.assertEqual(r.returncode, 1)
        self.assertIn("WORKFLOW.md", r.stderr)

    def test_startup_failure_is_clean(self):
        d = self.make_tmp()
        write_workflow(d, "tracker:\n  kind: nope")
        r = self.run_cli(cwd=d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsupported_tracker_kind", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_normal_start_and_shutdown(self):
        d = self.make_tmp()
        with open(os.path.join(d, "issues.yaml"), "w") as fh:
            fh.write("issues: []\n")
        path = write_workflow(d, "tracker:\n  kind: file\n  provider:\n    path: issues.yaml\n"
                                 "workspace:\n  root: ./ws")
        proc = subprocess.Popen([sys.executable, "-m", "symphony", path, "--port", "0"],
                                cwd=ROOT, stderr=subprocess.PIPE, text=True,
                                env={**os.environ, "PYTHONPATH": str(ROOT)})
        try:
            deadline = time.time() + 15
            lines = []
            while time.time() < deadline:
                line = proc.stderr.readline()
                lines.append(line)
                if "http_listening" in line:
                    break
            self.assertTrue(any("http_listening" in ln for ln in lines), "".join(lines))
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=15), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stderr.close()


class EndToEndTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_full_cycle_with_file_tracker_and_app_server(self):
        d = self.make_tmp()
        issues_path = os.path.join(d, "issues.yaml")
        with open(issues_path, "w") as fh:
            fh.write(textwrap.dedent("""
                issues:
                  - identifier: E2E-1
                    title: Make it work
                    state: Todo
                    priority: 1
            """))
        log_path = os.path.join(d, "fake.log")
        cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_SERVER))} tool_done " \
              f"{shlex.quote(log_path)}"
        path = write_workflow(d, f"""
            tracker:
              kind: file
              provider:
                path: issues.yaml
            polling:
              interval_ms: 60000
            workspace:
              root: ./ws
            hooks:
              after_create: echo created > .after_create
              before_run: echo run >> .runs
            agent:
              max_turns: 3
            codex:
              command: {json.dumps(cmd)}
              read_timeout_ms: 5000
        """, body="Ticket {{ issue.identifier }}: {{ issue.title }}")
        orch = Orchestrator(path)
        await orch.start()
        self.addAsyncCleanup(orch.stop)
        ws = os.path.join(d, "ws", "E2E-1")

        # The agent marks the issue Done via the tracker tool; the worker exits after one turn,
        # the continuation retry sees the terminal state, and the workspace is cleaned up.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if orch.completed and not orch.claimed and not os.path.exists(ws):
                break
        self.assertIn("E2E-1", orch.completed)
        self.assertEqual(orch.claimed, set())
        self.assertFalse(os.path.exists(ws))
        with open(issues_path) as fh:
            doc = yaml.safe_load(fh)
        self.assertEqual(doc["issues"][0]["state"], "Done")
        self.assertEqual(doc["issues"][0]["comments"], ["finished by fake agent"])
        with open(log_path) as fh:
            entries = [json.loads(line) for line in fh]
        turns = [e["recv"] for e in entries
                 if "recv" in e and e["recv"].get("method") == "turn/start"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["params"]["input"][0]["text"], "Ticket E2E-1: Make it work")
        snap = orch.snapshot()
        self.assertEqual(snap["codex_totals"]["total_tokens"], 110)
        self.assertEqual(snap["rate_limits"], {"primary": {"usedPercent": 12.5}})


if __name__ == "__main__":
    unittest.main()
