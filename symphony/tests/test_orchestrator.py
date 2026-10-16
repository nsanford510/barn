"""Spec 17.4: orchestrator dispatch, reconciliation, retry, and snapshot behavior."""

import asyncio
import dataclasses
import os
import unittest
from datetime import datetime, timezone

from helpers import (AgentError, FakeSession, MemoryTracker, TempDirMixin, make_issue,
                     write_workflow)

from symphony.orchestrator import Orchestrator, dispatch_sort_key, failure_backoff_ms


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


BASE = """
tracker:
  kind: memory
  {tracker_extra}
workspace:
  root: ./ws
agent:
  max_concurrent_agents: {max_agents}
  max_turns: {max_turns}
  max_retry_backoff_ms: {max_backoff}
  {agent_extra}
codex:
  stall_timeout_ms: {stall}
"""


class OrchestratorTestBase(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def make(self, issues=(), *, max_agents=10, max_turns=1, max_backoff=300000,
                   stall=300000, tracker_extra="", agent_extra="", body=None, behaviour=None,
                   start_error=None) -> Orchestrator:
        self.dir = self.make_tmp()
        self.front = BASE.format(max_agents=max_agents, max_turns=max_turns,
                                 max_backoff=max_backoff, stall=stall,
                                 tracker_extra=tracker_extra, agent_extra=agent_extra)
        self.path = write_workflow(self.dir, self.front, body or "Work on {{ issue.identifier }}")
        MemoryTracker.reset(list(issues))
        FakeSession.reset(behaviour, start_error)
        self.clock = FakeClock()
        orch = Orchestrator(self.path, session_factory=FakeSession, clock=self.clock)
        await orch.initialize()
        self.addAsyncCleanup(orch.stop)
        self.orch = orch
        return orch

    def ws_root(self):
        return os.path.join(self.dir, "ws")

    async def settle(self, rounds=50):
        """Let workers run and process every mailbox message they produce."""
        for _ in range(rounds):
            await asyncio.sleep(0.005)
            while not self.orch._mailbox.empty():
                await self.orch._handle(self.orch._mailbox.get_nowait())
            busy = any(e.task and not e.task.done() for e in self.orch.running.values())
            if not busy and self.orch._mailbox.empty() and not self.orch._stopping:
                return

    async def tick(self):
        await self.orch._handle(("tick",))

    async def fire_retry(self, issue_id):
        entry = self.orch.retry_attempts[issue_id]
        await self.orch._handle(("retry", issue_id, entry.token))


async def hang(session, issue, n):
    await asyncio.Event().wait()


class DispatchTest(OrchestratorTestBase):
    def test_sort_order(self):
        t = lambda d: datetime(2026, 1, d, tzinfo=timezone.utc)  # noqa: E731
        issues = [make_issue("D", priority=None, created_at=t(1)),
                  make_issue("C", priority=0, created_at=t(1)),
                  make_issue("B2", priority=2, created_at=t(3)),
                  make_issue("B1", priority=2, created_at=t(2)),
                  make_issue("A", priority=1, created_at=None),
                  make_issue("E", priority=2, created_at=t(2))]
        ordered = [i.identifier for i in sorted(issues, key=dispatch_sort_key)]
        self.assertEqual(ordered, ["A", "B1", "E", "B2", "C", "D"])

    async def test_eligibility_filters(self):
        await self.make([
            make_issue(1, labels=("agent",)),
            make_issue(2, labels=("AGENT",)),  # normalized case-insensitively by adapter rules
            make_issue(3, labels=("other",)),
            make_issue(4, labels=("agent",), dispatchable=False),
            make_issue(5, state="Backlog", labels=("agent",)),
            make_issue(6, state="Done", labels=("agent",)),
        ], tracker_extra="required_labels: [' Agent ']", behaviour=hang)
        await self.tick()
        self.assertEqual(sorted(e.identifier for e in self.orch.running.values()),
                         ["T-1", "T-2"])

    async def test_global_and_per_state_limits(self):
        issues = [make_issue(i, state="Todo") for i in range(1, 4)] + \
                 [make_issue(i, state="In Progress") for i in range(4, 7)]
        await self.make(issues, max_agents=4, behaviour=hang,
                        agent_extra="max_concurrent_agents_by_state: {'in progress': 1}")
        await self.tick()
        states = sorted(e.issue.state for e in self.orch.running.values())
        self.assertEqual(states, ["In Progress", "Todo", "Todo", "Todo"])
        await self.tick()  # already claimed issues are never dispatched twice
        self.assertEqual(len(self.orch.running), 4)
        await asyncio.sleep(0.02)
        self.assertEqual(len(FakeSession.instances), 4)

    async def test_candidate_fetch_failure_skips_dispatch(self):
        await self.make([make_issue(1)])
        MemoryTracker.fail_states = True
        await self.tick()
        self.assertEqual(self.orch.running, {})
        self.assertIn("candidate fetch failed", self.orch.snapshot()["health"]["last_dispatch_error"])
        self.assertIsNotNone(self.orch._tick_handle)  # next tick still scheduled


class WorkerLifecycleTest(OrchestratorTestBase):
    async def test_normal_exit_schedules_continuation(self):
        await self.make([make_issue(1)])
        await self.tick()
        await self.settle()
        self.assertEqual(self.orch.running, {})
        retry = self.orch.retry_attempts["id-T-1"]
        self.assertEqual(retry.attempt, 1)
        self.assertIsNone(retry.error)
        self.assertAlmostEqual(retry.due_at_ms - self.clock.now * 1000, 1000)
        self.assertIn("id-T-1", self.orch.completed)
        self.assertIn("id-T-1", self.orch.claimed)
        self.assertTrue(FakeSession.instances[0].stopped)
        self.assertTrue(os.path.isdir(os.path.join(self.ws_root(), "T-1")))  # preserved

    async def test_continuation_turns_on_same_session(self):
        await self.make([make_issue(1)], max_turns=3)
        await self.tick()
        await self.settle()
        (session,) = FakeSession.instances
        self.assertEqual(len(session.turns), 3)
        self.assertEqual(session.turns[0], "Work on T-1")
        self.assertTrue(all("Continue working on T-1" in t for t in session.turns[1:]))
        self.assertNotIn("Work on T-1", session.turns[1])

    async def test_worker_stops_turns_when_issue_leaves_active(self):
        def move(session, issue, n):
            if n == 2:
                MemoryTracker.put(dataclasses.replace(issue, state="Human Review"))
        await self.make([make_issue(1)], max_turns=5, behaviour=move)
        await self.tick()
        await self.settle()
        self.assertEqual(len(FakeSession.instances[0].turns), 2)
        # continuation retry then releases because issue is no longer active
        await self.fire_retry("id-T-1")
        self.assertNotIn("id-T-1", self.orch.claimed)
        self.assertNotIn("id-T-1", self.orch.retry_attempts)

    async def test_abnormal_exit_exponential_backoff_with_cap(self):
        def fail(session, issue, n):
            raise AgentError("kaboom", category="turn_failed")
        await self.make([make_issue(1)], max_backoff=25000, behaviour=fail)
        await self.tick()
        await self.settle()
        delays = []
        for expected_attempt in (1, 2, 3):
            r = self.orch.retry_attempts["id-T-1"]
            self.assertEqual(r.attempt, expected_attempt)
            self.assertIn("kaboom", r.error)
            self.assertEqual(r.identifier, "T-1")
            delays.append(round(r.due_at_ms - self.clock.now * 1000))
            await self.fire_retry("id-T-1")
            await self.settle()
        self.assertEqual(delays, [10000, 20000, 25000])
        self.assertEqual(failure_backoff_ms(10, 300000), 300000)
        self.assertEqual(self.orch.snapshot()["retrying"][0]["attempt"], 4)

    async def test_template_error_fails_attempt(self):
        await self.make([make_issue(1)], body="{{ issue.missing_field }}")
        await self.tick()
        await self.settle()
        r = self.orch.retry_attempts["id-T-1"]
        self.assertEqual(r.attempt, 1)
        self.assertIn("template_render_error", r.error)
        self.assertEqual(FakeSession.instances, [])  # never launched the agent

    async def test_startup_failure_is_retried(self):
        await self.make([make_issue(1)],
                        start_error=AgentError("no codex", category="codex_not_found"))
        await self.tick()
        await self.settle()
        self.assertIn("codex_not_found", self.orch.retry_attempts["id-T-1"].error)


class ReconcileTest(OrchestratorTestBase):
    async def test_no_running_is_noop(self):
        await self.make([])
        MemoryTracker.calls.clear()
        await self.orch._reconcile_running()
        self.assertEqual(MemoryTracker.calls, [])

    async def test_active_refresh_updates_snapshot(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        MemoryTracker.put(make_issue(1, state="In Progress", title="Renamed"))
        await self.tick()
        entry = self.orch.running["id-T-1"]
        self.assertEqual((entry.issue.state, entry.issue.title), ("In Progress", "Renamed"))

    async def test_non_active_stops_without_cleanup(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        await asyncio.sleep(0.02)
        MemoryTracker.put(make_issue(1, state="Human Review"))
        await self.tick()
        await self.settle()
        self.assertEqual(self.orch.running, {})
        self.assertNotIn("id-T-1", self.orch.claimed)
        self.assertNotIn("id-T-1", self.orch.retry_attempts)
        self.assertTrue(os.path.isdir(os.path.join(self.ws_root(), "T-1")))
        self.assertTrue(FakeSession.instances[0].stopped)

    async def test_unroutable_stops_without_cleanup(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        await asyncio.sleep(0.02)
        MemoryTracker.put(make_issue(1, dispatchable=False))
        await self.tick()
        await self.settle()
        self.assertEqual(self.orch.running, {})
        self.assertTrue(os.path.isdir(os.path.join(self.ws_root(), "T-1")))

    async def test_terminal_stops_and_cleans_workspace(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        await asyncio.sleep(0.02)
        self.assertTrue(os.path.isdir(os.path.join(self.ws_root(), "T-1")))
        MemoryTracker.put(make_issue(1, state="Done"))
        await self.tick()
        await self.settle()
        self.assertEqual(self.orch.running, {})
        self.assertFalse(os.path.exists(os.path.join(self.ws_root(), "T-1")))

    async def test_missing_issue_stops_worker(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        MemoryTracker.issues.clear()
        await self.tick()
        await self.settle()
        self.assertEqual(self.orch.running, {})
        self.assertNotIn("id-T-1", self.orch.claimed)

    async def test_refresh_failure_keeps_workers(self):
        await self.make([make_issue(1)], behaviour=hang)
        await self.tick()
        MemoryTracker.fail_ids = True
        await self.tick()
        self.assertIn("id-T-1", self.orch.running)

    async def test_stall_detection_kills_and_retries(self):
        await self.make([make_issue(1)], stall=60000, behaviour=hang)
        await self.tick()
        await asyncio.sleep(0.02)
        self.clock.now += 59
        await self.tick()
        self.assertIn("id-T-1", self.orch.running)
        self.clock.now += 2
        await self.tick()
        await self.settle()
        self.assertNotIn("id-T-1", self.orch.running)
        r = self.orch.retry_attempts["id-T-1"]
        self.assertEqual(r.attempt, 1)
        self.assertIn("stalled", r.error)
        self.assertTrue(FakeSession.instances[0].stopped)

    async def test_stall_detection_disabled(self):
        await self.make([make_issue(1)], stall=0, behaviour=hang)
        await self.tick()
        self.clock.now += 10_000
        await self.tick()
        self.assertIn("id-T-1", self.orch.running)


class RetryTest(OrchestratorTestBase):
    async def test_slot_exhaustion_requeues_with_reason(self):
        await self.make([make_issue(1)], max_agents=1, behaviour=hang)
        self.orch._schedule_retry("id-T-2", 1, make_issue(2), error="x")
        MemoryTracker.put(make_issue(2))
        await self.tick()  # T-1 takes the only slot
        await self.fire_retry("id-T-2")
        r = self.orch.retry_attempts["id-T-2"]
        self.assertEqual((r.attempt, r.error), (2, "no available orchestrator slots"))

    async def test_retry_dispatches_with_attempt(self):
        await self.make([make_issue(1)], body="attempt={{ attempt }}", behaviour=hang)
        self.orch._schedule_retry("id-T-1", 2, make_issue(1), error="x")
        await self.fire_retry("id-T-1")
        await asyncio.sleep(0.02)
        self.assertEqual(self.orch.running["id-T-1"].retry_attempt, 2)
        self.assertEqual(FakeSession.instances[0].turns[0], "attempt=2")

    async def test_retry_releases_missing_and_cleans_terminal(self):
        await self.make([make_issue(2, state="Done")])
        os.makedirs(os.path.join(self.ws_root(), "T-2"))
        self.orch._schedule_retry("id-T-1", 1, make_issue(1), error="x")
        self.orch._schedule_retry("id-T-2", 1, make_issue(2), error="x")
        await self.fire_retry("id-T-1")
        await self.fire_retry("id-T-2")
        self.assertEqual(self.orch.claimed, set())
        self.assertFalse(os.path.exists(os.path.join(self.ws_root(), "T-2")))

    async def test_retry_refresh_failure_requeues(self):
        await self.make([make_issue(1)])
        self.orch._schedule_retry("id-T-1", 1, make_issue(1), error="x")
        MemoryTracker.fail_ids = True
        await self.fire_retry("id-T-1")
        r = self.orch.retry_attempts["id-T-1"]
        self.assertEqual((r.attempt, r.error), (2, "retry refresh failed"))


class StartupAndReloadTest(OrchestratorTestBase):
    async def test_startup_terminal_cleanup(self):
        d = self.make_tmp()
        os.makedirs(os.path.join(d, "ws", "T-9"))
        os.makedirs(os.path.join(d, "ws", "T-1"))
        path = write_workflow(d, "tracker:\n  kind: memory\nworkspace:\n  root: ./ws")
        MemoryTracker.reset([make_issue(9, state="Done"), make_issue(1)])
        orch = Orchestrator(path, session_factory=FakeSession)
        await orch.initialize()
        self.assertFalse(os.path.exists(os.path.join(d, "ws", "T-9")))
        self.assertTrue(os.path.exists(os.path.join(d, "ws", "T-1")))

    async def test_startup_cleanup_failure_is_not_fatal(self):
        d = self.make_tmp()
        path = write_workflow(d, "tracker:\n  kind: memory")
        MemoryTracker.reset([])
        MemoryTracker.fail_states = True
        orch = Orchestrator(path, session_factory=FakeSession)
        await orch.initialize()  # no exception

    async def test_reload_applies_and_invalid_reload_keeps_last_good(self):
        await self.make([make_issue(1), make_issue(2)], max_agents=1, behaviour=hang)
        self.assertEqual(self.orch.max_concurrent_agents, 1)
        write_workflow(self.dir, self.front.replace("max_concurrent_agents: 1",
                                                    "max_concurrent_agents: 5")
                       + "polling:\n  interval_ms: 1234\n")
        self.assertTrue(self.orch.maybe_reload())
        self.assertEqual((self.orch.max_concurrent_agents, self.orch.poll_interval_ms), (5, 1234))

        with open(self.path, "w") as fh:
            fh.write("---\ntracker: [broken\n---\nbody\n")
        self.assertFalse(self.orch.maybe_reload())
        self.assertEqual(self.orch.max_concurrent_agents, 5)  # last known good kept
        self.assertIn("workflow_parse_error", self.orch.workflow_error)
        await self.tick()
        self.assertEqual(self.orch.running, {})  # dispatch blocked until fixed
        self.assertIn("workflow_parse_error",
                      self.orch.snapshot()["health"]["last_dispatch_error"])

        write_workflow(self.dir, self.front.replace("max_concurrent_agents: 1",
                                                    "max_concurrent_agents: 5"))
        self.assertTrue(self.orch.maybe_reload())
        await self.tick()
        self.assertEqual(len(self.orch.running), 2)

    async def test_startup_validation_failure_raises(self):
        d = self.make_tmp()
        path = write_workflow(d, "tracker:\n  kind: nope")
        with self.assertRaises(Exception):
            await Orchestrator(path, session_factory=FakeSession).initialize()


class SnapshotTest(OrchestratorTestBase):
    async def test_snapshot_rows_and_token_accounting(self):
        await self.make([make_issue(1, url="https://t/1")], behaviour=hang)
        await self.tick()
        await asyncio.sleep(0.02)
        entry = self.orch.running["id-T-1"]
        emit = lambda **ev: self.orch._on_codex_event("id-T-1", entry.token,  # noqa: E731
                                                      {"event": "notification", **ev})
        emit(method="thread/tokenUsage/updated",
             usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})
        emit(method="thread/tokenUsage/updated",
             usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})  # repeat
        emit(method="thread/tokenUsage/updated",
             usage={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180},
             rate_limits={"primary": {"usedPercent": 40}}, message="working")
        self.orch._on_codex_event("id-T-1", "stale-token", {"event": "notification",
                                                            "usage": {"total_tokens": 99999}})
        self.clock.now += 5
        snap = self.orch.snapshot()
        self.assertEqual(snap["counts"]["running"], 1)
        row = snap["running"][0]
        self.assertEqual(row["issue_url"], "https://t/1")
        self.assertEqual(row["turn_count"], 1)
        self.assertEqual(row["session_id"], "thr-t1")
        self.assertEqual(row["last_message"], "working")
        self.assertEqual(row["tokens"], {"input_tokens": 150, "output_tokens": 30,
                                         "total_tokens": 180})
        self.assertEqual(snap["codex_totals"]["total_tokens"], 180)
        self.assertGreaterEqual(snap["codex_totals"]["seconds_running"], 5)
        self.assertEqual(snap["rate_limits"], {"primary": {"usedPercent": 40}})

        detail = self.orch.issue_detail("T-1")
        self.assertEqual(detail["status"], "running")
        self.assertTrue(detail["workspace"]["path"].endswith("/T-1"))
        self.assertTrue(detail["recent_events"])
        self.assertIsNone(self.orch.issue_detail("NOPE-1"))

    async def test_snapshot_retry_rows(self):
        await self.make([])
        self.orch._schedule_retry("id-T-3", 2, make_issue(3, url="u"), error="boom")
        row = self.orch.snapshot()["retrying"][0]
        self.assertEqual({k: row[k] for k in ("issue_identifier", "attempt", "error", "issue_url")},
                         {"issue_identifier": "T-3", "attempt": 2, "error": "boom",
                          "issue_url": "u"})
        self.assertTrue(row["due_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
