"""Spec 17.2: workspace manager and safety."""

import os
import unittest

from helpers import TempDirMixin

from symphony.config import CodexConfig, HooksConfig
from symphony.codex import AppServerSession
from symphony.errors import AgentError, WorkspaceError
from symphony.workspace import WorkspaceManager, ensure_contained, workspace_key


class WorkspaceKeyTest(unittest.TestCase):
    def test_unchanged_identifier_keeps_key(self):
        self.assertEqual(workspace_key("ABC-123"), "ABC-123")
        self.assertEqual(workspace_key("a.b_c-1"), "a.b_c-1")

    def test_sanitized_gets_stable_hash(self):
        k = workspace_key("ABC/123")
        self.assertRegex(k, r"^ABC_123-[0-9a-f]{16}$")
        self.assertEqual(k, workspace_key("ABC/123"))

    def test_collisions_after_sanitization_are_distinct(self):
        keys = {workspace_key(i) for i in ("A/1", "A:1", "A 1", "A_1")}
        self.assertEqual(len(keys), 4)

    def test_dot_names_never_traverse(self):
        for ident in ("..", ".", "../etc", "a/../../b"):
            k = workspace_key(ident)
            self.assertNotIn("/", k)
            self.assertNotIn(k, (".", ".."))


class WorkspaceManagerTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    def mgr(self, **hooks) -> tuple[WorkspaceManager, str]:
        root = os.path.join(self.make_tmp(), "root")
        return WorkspaceManager(root, HooksConfig(timeout_ms=hooks.pop("timeout_ms", 5000),
                                                  **hooks)), root

    async def test_create_then_reuse(self):
        m, root = self.mgr()
        ws = await m.create_for_issue("T-1")
        self.assertTrue(ws.created_now)
        self.assertEqual(ws.path, os.path.realpath(os.path.join(root, "T-1")))
        self.assertTrue(os.path.isdir(ws.path))
        again = await m.create_for_issue("T-1")
        self.assertFalse(again.created_now)
        self.assertEqual(again.path, ws.path)

    async def test_non_directory_at_path_fails(self):
        m, root = self.mgr()
        os.makedirs(root)
        open(os.path.join(root, "T-1"), "w").close()
        with self.assertRaises(WorkspaceError):
            await m.create_for_issue("T-1")

    async def test_symlink_escape_rejected(self):
        m, root = self.mgr()
        os.makedirs(root)
        outside = self.make_tmp()
        os.symlink(outside, os.path.join(root, "T-1"))
        with self.assertRaises(WorkspaceError):
            await m.create_for_issue("T-1")

    async def test_after_create_only_on_new(self):
        m, _ = self.mgr(after_create="echo x >> created.txt")
        ws = await m.create_for_issue("T-1")
        await m.create_for_issue("T-1")
        with open(os.path.join(ws.path, "created.txt")) as fh:
            self.assertEqual(fh.read().count("x"), 1)

    async def test_after_create_failure_is_fatal_and_cleans(self):
        m, root = self.mgr(after_create="exit 7")
        with self.assertRaises(WorkspaceError):
            await m.create_for_issue("T-1")
        self.assertFalse(os.path.exists(os.path.join(root, "T-1")))

    async def test_before_run_failure_and_timeout(self):
        m, _ = self.mgr(before_run="exit 1")
        ws = await m.create_for_issue("T-1")
        with self.assertRaises(WorkspaceError) as cm:
            await m.before_run(ws.path)
        self.assertEqual(cm.exception.category, "hook_failed")
        m2, _ = self.mgr(before_run="sleep 5", timeout_ms=200)
        ws2 = await m2.create_for_issue("T-2")
        with self.assertRaises(WorkspaceError) as cm:
            await m2.before_run(ws2.path)
        self.assertEqual(cm.exception.category, "hook_timeout")

    async def test_hook_runs_in_workspace_cwd(self):
        m, _ = self.mgr(before_run="pwd > where.txt")
        ws = await m.create_for_issue("T-1")
        await m.before_run(ws.path)
        with open(os.path.join(ws.path, "where.txt")) as fh:
            self.assertEqual(os.path.realpath(fh.read().strip()), ws.path)

    async def test_after_run_failure_ignored(self):
        m, _ = self.mgr(after_run="exit 3")
        ws = await m.create_for_issue("T-1")
        await m.after_run(ws.path)  # no exception
        m2, _ = self.mgr(after_run="sleep 5", timeout_ms=200)
        ws2 = await m2.create_for_issue("T-1")
        await m2.after_run(ws2.path)

    async def test_remove_runs_before_remove_and_ignores_failure(self):
        marker = os.path.join(self.make_tmp(), "marker")
        m, _ = self.mgr(before_remove=f"touch {marker}; exit 9")
        ws = await m.create_for_issue("T-1")
        self.assertTrue(await m.remove("T-1"))
        self.assertTrue(os.path.exists(marker))
        self.assertFalse(os.path.exists(ws.path))
        self.assertFalse(await m.remove("T-1"))

    async def test_hooks_do_not_inherit_tracker_secrets(self):
        os.environ["SYMPHONY_TEST_SECRET"] = "s3cret"
        self.addCleanup(os.environ.pop, "SYMPHONY_TEST_SECRET", None)
        root = os.path.join(self.make_tmp(), "root")
        m = WorkspaceManager(root, HooksConfig(before_run='echo "[$SYMPHONY_TEST_SECRET]" > e.txt'),
                             ["SYMPHONY_TEST_SECRET"])
        ws = await m.create_for_issue("T-1")
        await m.before_run(ws.path)
        with open(os.path.join(ws.path, "e.txt")) as fh:
            self.assertEqual(fh.read().strip(), "[]")


class ContainmentTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    def test_ensure_contained(self):
        root = self.make_tmp()
        os.makedirs(os.path.join(root, "a"))
        ensure_contained(root, os.path.join(root, "a"))
        for bad in (root, os.path.dirname(root), os.path.join(root, "..", "x")):
            with self.assertRaises(WorkspaceError):
                ensure_contained(root, bad)

    async def test_agent_launch_rejects_out_of_root_cwd(self):
        root, outside = self.make_tmp(), self.make_tmp()
        s = AppServerSession(CodexConfig(command="true"), workspace_path=outside,
                             workspace_root=root, on_event=lambda e: None)
        with self.assertRaises(AgentError) as cm:
            from helpers import make_issue
            await s.start(make_issue(1))
        self.assertEqual(cm.exception.category, "invalid_workspace_cwd")
        self.assertIsNone(s.proc)


if __name__ == "__main__":
    unittest.main()
