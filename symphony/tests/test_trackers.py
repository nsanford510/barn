"""Spec 17.3: issue tracker adapters (Linear via mocked HTTP, local file tracker)."""

import json
import os
import unittest

import httpx
from helpers import TempDirMixin, make_issue, write_workflow

from symphony.config import ServiceConfig
from symphony.errors import TrackerError
from symphony.trackers import build_tracker
from symphony.trackers.base import TrackerPolicy
from symphony.trackers.linear import LinearTracker
from symphony.workflow import load_workflow


def node(n, state="Todo", **kw):
    base = {
        "id": f"lin-{n}", "identifier": f"ENG-{n}", "title": f"Title {n}",
        "description": None, "priority": 2, "url": f"https://linear.app/x/ENG-{n}",
        "branchName": f"eng-{n}", "createdAt": "2026-01-0%dT00:00:00.000Z" % (n % 9 + 1),
        "updatedAt": "not-a-date", "state": {"name": state}, "assignee": {"id": "u1"},
        "team": {"key": "ENG"}, "project": {"slugId": "proj"},
        "labels": {"nodes": [{"name": " Agent "}, {"name": "agent"}, {"name": ""}, {"x": 1}]},
        "inverseRelations": {"nodes": []},
    }
    base.update(kw)
    return base


def page(nodes, has_next=False, cursor=None):
    return {"data": {"issues": {"nodes": nodes,
                                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}}}


class LinearTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    def tracker(self, handler, provider_extra="", env=None) -> LinearTracker:
        os.environ["LINEAR_API_KEY_TEST"] = "lin_api_secret"
        self.addCleanup(os.environ.pop, "LINEAR_API_KEY_TEST", None)
        d = self.make_tmp()
        wf = load_workflow(write_workflow(d, f"""
            tracker:
              kind: linear
              provider:
                api_key: $LINEAR_API_KEY_TEST
                project_slug: proj
                page_size: 2
                {provider_extra}
        """))
        cfg = ServiceConfig.from_workflow(wf)
        self.requests = []

        def wrapped(request: httpx.Request):
            body = json.loads(request.content)
            self.requests.append((request, body))
            return handler(body)
        return LinearTracker(cfg, transport=httpx.MockTransport(wrapped))

    async def test_empty_inputs_make_no_requests(self):
        t = self.tracker(lambda b: httpx.Response(500))
        self.assertEqual(await t.fetch_issues_by_states([]), [])
        self.assertEqual(await t.fetch_issues_by_ids([]), [])
        self.assertEqual(self.requests, [])

    async def test_pagination_scope_order_and_normalization(self):
        pages = [page([node(1), node(2)], True, "c1"), page([node(3, "In Progress")])]
        t = self.tracker(lambda b: httpx.Response(200, json=pages.pop(0)))
        issues = await t.fetch_issues_by_states(["Todo", "In Progress"])
        self.assertEqual([i.identifier for i in issues], ["ENG-1", "ENG-2", "ENG-3"])
        req, body = self.requests[0]
        self.assertEqual(req.headers["Authorization"], "lin_api_secret")
        self.assertEqual(body["variables"]["projectSlug"], "proj")
        self.assertEqual(body["variables"]["states"], ["Todo", "In Progress"])
        self.assertEqual(self.requests[1][1]["variables"]["after"], "c1")
        i = issues[0]
        self.assertEqual(i.labels, ("agent",))
        self.assertIsNone(i.updated_at)  # unusable optional -> null
        self.assertIsNotNone(i.created_at)
        self.assertEqual(i.priority, 2)
        self.assertEqual(i.native_ref["issue_id"], "lin-1")
        self.assertTrue(i.dispatchable)

    async def test_malformed_state_record_is_omitted(self):
        bad = node(2)
        bad["title"] = ""
        t = self.tracker(lambda b: httpx.Response(200, json=page([node(1), bad])))
        with self.assertLogs("symphony.tracker.linear", "WARNING") as logs:
            issues = await t.fetch_issues_by_states(["Todo"])
        self.assertEqual([i.identifier for i in issues], ["ENG-1"])
        self.assertIn("tracker_record_omitted", logs.output[0])

    async def test_malformed_id_refresh_fails(self):
        bad = node(1, state=None)
        t = self.tracker(lambda b: httpx.Response(200, json=page([bad])))
        with self.assertRaises(TrackerError) as cm:
            await t.fetch_issues_by_ids(["lin-1"])
        self.assertEqual(cm.exception.category, "tracker_response")

    async def test_refresh_returns_full_snapshots_and_chunks(self):
        def handler(body):
            return httpx.Response(200, json=page([node(int(i.split("-")[1]))
                                                  for i in body["variables"]["ids"]]))
        t = self.tracker(handler)
        issues = await t.fetch_issues_by_ids(["lin-1", "lin-2", "lin-3", "lin-1"])
        self.assertEqual(sorted(i.id for i in issues), ["lin-1", "lin-2", "lin-3"])
        self.assertEqual(len(self.requests), 2)  # page_size=2 chunks
        self.assertEqual(issues[0].title, "Title 1")

    async def test_blockers_and_assignee_drive_dispatchable(self):
        blocked = node(1, inverseRelations={"nodes": [
            {"type": "blocks", "issue": {"id": "b", "identifier": "ENG-9",
                                         "state": {"name": "In Progress"}}},
            {"type": "related", "issue": {"id": "r", "identifier": "ENG-8",
                                          "state": {"name": "Todo"}}}]})
        done_blocker = node(2, inverseRelations={"nodes": [
            {"type": "blocks", "issue": {"id": "b2", "identifier": "ENG-7",
                                         "state": {"name": "Done"}}}]})
        in_progress_blocked = dict(blocked, id="lin-3", identifier="ENG-3",
                                   state={"name": "In Progress"})
        other_assignee = node(4, assignee={"id": "someone-else"})

        def handler(body):
            if "viewer" in body["query"]:
                return httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}})
            return httpx.Response(200, json=page([blocked, done_blocker, in_progress_blocked,
                                                  other_assignee]))
        t = self.tracker(handler, provider_extra="assignee: me")
        by_id = {i.identifier: i for i in await t.fetch_issues_by_states(["Todo"])}
        self.assertFalse(by_id["ENG-1"].dispatchable)
        self.assertEqual([b.identifier for b in by_id["ENG-1"].blocked_by], ["ENG-9"])
        self.assertTrue(by_id["ENG-2"].dispatchable)
        self.assertTrue(by_id["ENG-3"].dispatchable)  # only Todo is blocker-gated
        self.assertFalse(by_id["ENG-4"].dispatchable)

    async def test_error_mapping(self):
        cases = [
            (lambda b: httpx.Response(429, headers={"retry-after": "2"}), "tracker_rate_limited"),
            (lambda b: httpx.Response(503), "tracker_status"),
            (lambda b: httpx.Response(200, text="<html>"), "tracker_response"),
            (lambda b: httpx.Response(200, json={"errors": [{"message": "x"}]}),
             "tracker_response"),
            (lambda b: httpx.Response(200, json={"errors": [
                {"message": "slow", "extensions": {"code": "RATELIMITED"}}]}),
             "tracker_rate_limited"),
            (lambda b: httpx.Response(200, json=page([node(1)], True, None)),
             "tracker_pagination"),
        ]
        for handler, category in cases:
            t = self.tracker(handler)
            with self.assertRaises(TrackerError) as cm:
                await t.fetch_issues_by_states(["Todo"])
            self.assertEqual(cm.exception.category, category)

        def boom(b):
            raise httpx.ConnectError("down")
        t = self.tracker(boom)
        with self.assertRaises(TrackerError) as cm:
            await t.fetch_issues_by_states(["Todo"])
        self.assertEqual(cm.exception.category, "tracker_request")

    async def test_config_errors(self):
        d = self.make_tmp()
        os.environ.pop("LINEAR_API_KEY_EMPTY", None)
        for provider, category in [
            ("api_key: $LINEAR_API_KEY_EMPTY\n    project_slug: p", "missing_tracker_secret"),
            ("api_key: k", "invalid_tracker_config"),
            ("api_key: k\n    project_slug: p\n    page_size: 0", "invalid_tracker_config"),
        ]:
            wf = load_workflow(write_workflow(
                d, f"tracker:\n  kind: linear\n  provider:\n    {provider}"))
            with self.assertRaises(TrackerError) as cm:
                build_tracker(ServiceConfig.from_workflow(wf))
            self.assertEqual(cm.exception.category, category)

    async def test_secret_names_and_tool(self):
        def handler(body):
            if "mutation" in body["query"]:
                return httpx.Response(200, json={"data": {"commentCreate": {"success": True}}})
            return httpx.Response(200, json={"errors": [{"message": "bad field"}]})
        t = self.tracker(handler)
        self.assertEqual(t.secret_environment_names(), ["LINEAR_API_KEY", "LINEAR_API_KEY_TEST"])
        self.assertEqual([s.name for s in t.agent_tool_specs()], ["linear_graphql"])
        ctx = {"issue": make_issue(1)}
        ok = await t.execute_agent_tool("linear_graphql", {"query": "mutation { x }"}, ctx)
        self.assertTrue(ok.success)
        bad = await t.execute_agent_tool("linear_graphql", {"query": "{ y }"}, ctx)
        self.assertFalse(bad.success)
        self.assertIn("errors", bad.output)
        inv = await t.execute_agent_tool("linear_graphql", {"variables": {}}, ctx)
        self.assertEqual(inv.output["error"]["category"], "invalid_arguments")
        unk = await t.execute_agent_tool("nope", {}, ctx)
        self.assertEqual(unk.output["error"]["category"], "unsupported_tool")


class FileTrackerTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    async def test_reads_filters_and_updates(self):
        d = self.make_tmp()
        with open(os.path.join(d, "issues.yaml"), "w") as fh:
            fh.write("""
issues:
  - identifier: LOC-1
    title: First
    state: Todo
    labels: [Agent]
    created_at: 2026-01-01T00:00:00Z
  - identifier: LOC-2
    title: Blocked
    state: Todo
    blocked_by: [{identifier: LOC-1, state: Todo}]
  - identifier: LOC-3
    title: Finished
    state: Done
  - title: missing identifier
    state: Todo
""")
        wf = load_workflow(write_workflow(d, "tracker:\n  kind: file\n  provider:\n    path: issues.yaml"))
        t = build_tracker(ServiceConfig.from_workflow(wf))
        todo = await t.fetch_issues_by_states(["todo"])
        self.assertEqual([i.identifier for i in todo], ["LOC-1", "LOC-2"])
        self.assertTrue(todo[0].dispatchable)
        self.assertFalse(todo[1].dispatchable)
        self.assertEqual(todo[0].labels, ("agent",))
        self.assertIsNotNone(todo[0].created_at)
        self.assertEqual([i.identifier for i in await t.fetch_issues_by_states(["Done"])], ["LOC-3"])
        res = await t.execute_agent_tool("file_tracker_update",
                                         {"state": "Done", "comment": "shipped"},
                                         {"issue": todo[0]})
        self.assertTrue(res.success)
        (fresh,) = await t.fetch_issues_by_ids(["LOC-1"])
        self.assertEqual(fresh.state, "Done")


class PolicyTest(unittest.TestCase):
    def test_required_labels_and_states(self):
        p = TrackerPolicy(["Todo", "In Progress"], ["Done"], ["agent"])
        self.assertTrue(p.routable(make_issue(1, labels=("agent", "x"))))
        self.assertFalse(p.routable(make_issue(1, labels=("x",))))
        self.assertFalse(p.routable(make_issue(1, labels=("agent",), dispatchable=False)))
        self.assertTrue(p.is_active(make_issue(1, state=" in progress ")))
        self.assertFalse(p.is_active(make_issue(1, state="Done")))
        blank = TrackerPolicy(["Todo"], [], [""])
        self.assertFalse(blank.routable(make_issue(1, labels=("agent",))))


if __name__ == "__main__":
    unittest.main()
