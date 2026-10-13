from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

SCAN_DIR = Path(__file__).resolve().parents[1]
for path in (SCAN_DIR, SCAN_DIR / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fleet_fakes import FakeDb, FakeScanner, FakeWorkerState
from fleet import fleet_node as fn


class _Artifacts:
    def __init__(self) -> None:
        self.restored = 0
        self.uploads = 0

    def restore(self):
        self.restored += 1
        return []

    def upload(self):
        self.uploads += 1
        return []


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_node(main_impl=None, sleep=None, clock=None, parcels=3, **kwargs):
    db = FakeDb()
    scanner = FakeScanner(main_impl, parcels=parcels)
    artifacts = _Artifacts()
    node = fn.FleetNode(
        run_id="run1", index=2, count=4, scanner=scanner, worker_state=FakeWorkerState(db), db=db,
        artifacts=artifacts, info={"instance_id": "i-abc", "public_ip": "9.9.9.9", "az": "us-west-2b"},
        sleep=sleep or (lambda seconds: None), clock=clock or _Clock(), shutdown=MagicMock(), **kwargs,
    )
    return node, db, scanner, artifacts


def job_row(db: FakeDb, index: int = 0) -> dict:
    return db.store["scanner_jobs"][index]


class FleetNodeTests(unittest.TestCase):
    def test_register_sets_worker_name_and_capabilities(self) -> None:
        node, db, _, _ = make_node()
        node.register()

        worker = db.store["scanner_workers"][0]
        self.assertEqual(worker["name"], "barn-scan-node-2")
        self.assertEqual(worker["capabilities"], {
            "instance_id": "i-abc", "public_ip": "9.9.9.9", "az": "us-west-2b", "run_id": "run1", "segment": 2, "of": 4,
        })

    def test_ensure_job_creates_then_reuses_the_nodes_job_for_the_run(self) -> None:
        node, db, _, _ = make_node()
        node.register()
        first = node.ensure_job(500)
        self.assertEqual(job_row(db)["scope"]["total"], 500)
        self.assertEqual((job_row(db)["job_type"], job_row(db)["status"]), ("fleet_scan", "running"))

        again, _, _, _ = make_node()
        again.db = db
        again.ws = FakeWorkerState(db)
        again.register()
        self.assertEqual(again.ensure_job(500), first)
        self.assertEqual(len(db.store["scanner_jobs"]), 1)

        other_run, _, _, _ = make_node()
        other_run.db, other_run.ws, other_run.run_id = db, FakeWorkerState(db), "run2"
        other_run.register()
        self.assertNotEqual(other_run.ensure_job(500), first)

    def test_tick_reports_progress_and_throttles_checkpoints(self) -> None:
        clock = _Clock()
        node, db, scanner, artifacts = make_node(clock=clock)
        node.register()
        node.ensure_job(3)
        scanner.scan_progress.update(scanned=7, added=2, removed=3, unflagged=1, errors=1, rejections=1,
                                     per_min=5.9, current_apn="010 083101100", total=30)

        node.tick()
        self.assertEqual((job_row(db)["processed_count"], job_row(db)["hit_count"]), (7, 2))
        self.assertEqual(job_row(db)["current_apn"], "010 083101100")
        checkpoints = db.store["scanner_job_checkpoints"]
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["row_offset"], 7)
        self.assertEqual(checkpoints[0]["extra"]["per_min"], 5.9)
        self.assertEqual(checkpoints[0]["extra"]["removed"], 3)
        self.assertEqual(checkpoints[0]["extra"]["total"], 30)

        clock.now += fn.CHECKPOINT_SEC - 1
        node.tick()
        self.assertEqual(len(checkpoints), 1)         # too soon for another checkpoint
        clock.now += 2
        node.tick()
        self.assertEqual(len(checkpoints), 2)

    def test_tick_reports_nothing_before_the_scan_has_started(self) -> None:
        node, db, scanner, _ = make_node()
        node.register()
        node.ensure_job(3)
        node.ws.update_job(node.job_id, processed_count=2650)          # progress from before a restart
        scanner.scan_progress.update(scanned=0, total=0)               # main() has not begun (start delay)

        node.tick()

        self.assertEqual(job_row(db)["processed_count"], 2650)          # not overwritten with a false zero
        self.assertEqual(db.store.get("scanner_job_checkpoints", []), [])

    def test_tick_backs_up_artifacts_periodically(self) -> None:
        clock = _Clock()
        node, _, _, artifacts = make_node(clock=clock)
        node.register()
        node.ensure_job(3)
        node.tick()
        self.assertEqual(artifacts.uploads, 0)
        clock.now += fn.UPLOAD_SEC
        node.tick()
        self.assertEqual(artifacts.uploads, 1)

    def test_tick_survives_database_errors(self) -> None:
        node, db, scanner, _ = make_node()
        node.register()
        node.ensure_job(3)
        node.ws = MagicMock()
        node.ws.update_job.side_effect = RuntimeError("supabase down")
        node.tick()  # must not raise

    def test_run_to_completion(self) -> None:
        def main(scanner: FakeScanner) -> dict:
            scanner.scan_progress.update(scanned=3, added=1)
            return {"scanned": 3, "added": 1}

        node, db, scanner, artifacts = make_node(main, shutdown_when_done=True)
        self.assertEqual(node.run(), 0)

        self.assertEqual(scanner.main_calls, [{"city": "OAKLAND", "restrict_to_csv": True}])
        self.assertEqual(job_row(db)["status"], "completed")
        self.assertEqual(job_row(db)["processed_count"], 3)
        self.assertEqual(db.store["scanner_workers"][0]["status"], "offline")
        self.assertEqual(artifacts.restored, 1)
        self.assertGreaterEqual(artifacts.uploads, 1)
        node._shutdown.assert_called_once()

    def test_no_shutdown_unless_requested(self) -> None:
        node, _, _, _ = make_node(lambda scanner: {})
        node.run()
        node._shutdown.assert_not_called()

    def test_start_delay_is_honoured(self) -> None:
        sleeps: list[float] = []
        node, _, _, _ = make_node(lambda scanner: {}, sleep=sleeps.append, start_delay_sec=240)
        node.run()
        self.assertEqual(sleeps[0], 240)

    def test_blocked_scan_marks_job_failed_and_waits_for_resume(self) -> None:
        holder: dict = {}
        seen: dict = {}

        def main(scanner: FakeScanner) -> dict:
            return {"aborted": 1}

        def operator_sleep(seconds: float) -> None:
            node = holder["node"]
            if seconds == fn.IDLE_POLL_SEC and "failed" not in seen:
                seen["failed"] = dict(job_row(node.db))        # node is idle: job must read failed
                node.ws.update_job(node.job_id, status="completed")  # operator ends the run

        node, db, scanner, _ = make_node(main, sleep=operator_sleep)
        holder["node"] = node
        self.assertEqual(node.run(), 0)

        self.assertEqual(seen["failed"]["status"], "failed")
        self.assertIn("blocked", seen["failed"]["error_summary"])
        self.assertEqual(len(scanner.main_calls), 1)        # no automatic retry, no relaunch

    def test_stop_from_ui_pauses_and_resume_restarts_the_scan(self) -> None:
        holder: dict = {}
        calls = {"n": 0}

        def main(scanner: FakeScanner) -> dict:
            calls["n"] += 1
            node = holder["node"]
            if calls["n"] == 1:
                node.ws.update_job(node.job_id, status="interrupted")   # "Stop fleet" pressed
                node.tick()                                              # reporter notices
                return {}                                                # scanner stopped early
            return {"scanned": 3}

        def operator_sleep(seconds: float) -> None:
            node = holder["node"]
            if seconds == fn.IDLE_POLL_SEC:
                node.ws.update_job(node.job_id, status="running")        # "Resume fleet" pressed

        node, db, scanner, _ = make_node(main, sleep=operator_sleep)
        holder["node"] = node
        self.assertEqual(node.run(), 0)

        self.assertEqual(scanner.stop_calls, 1)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(job_row(db)["status"], "completed")

    def test_crash_propagates_so_systemd_restarts_the_node(self) -> None:
        def main(scanner: FakeScanner) -> dict:
            raise RuntimeError("boom")

        node, db, _, _ = make_node(main)
        with self.assertRaises(RuntimeError):
            node.run()
        self.assertIn("boom", job_row(db)["error_summary"])
        self.assertEqual(job_row(db)["status"], "running")   # resumable

    def test_restart_after_completion_finishes_without_rescanning(self) -> None:
        node, db, scanner, _ = make_node(lambda s: {}, shutdown_when_done=True)
        node.register()
        node.ensure_job(3)
        node.ws.update_job(node.job_id, status="completed")
        self.assertEqual(node.run(), 0)
        self.assertEqual(scanner.main_calls, [])
        node._shutdown.assert_called_once()

    def test_failed_lookups_are_retried_in_further_passes_before_completing(self) -> None:
        calls = {"n": 0}

        def main(scanner: FakeScanner) -> dict:
            calls["n"] += 1
            return {"error": 3} if calls["n"] < 3 else {"error": 0}

        sleeps: list[float] = []
        node, db, scanner, _ = make_node(main, sleep=sleeps.append)
        self.assertEqual(node.run(), 0)

        self.assertEqual(calls["n"], 3)
        self.assertEqual(node.passes, 2)
        self.assertEqual(sleeps.count(fn.RETRY_PAUSE_SEC), 2)             # a pause before each retry pass
        self.assertEqual(job_row(db)["status"], "completed")
        self.assertIsNone(job_row(db).get("error_summary"))

    def test_gives_up_after_the_retry_budget_and_says_so(self) -> None:
        node, db, scanner, _ = make_node(lambda s: {"error": 5})
        self.assertEqual(node.run(), 0)

        self.assertEqual(len(scanner.main_calls), 1 + fn.MAX_RETRY_PASSES)
        self.assertEqual(job_row(db)["status"], "completed")
        self.assertIn("5 lookups still failing", job_row(db)["error_summary"])

    def test_checkpoint_reports_memory_load_and_retry_pass(self) -> None:
        from unittest.mock import patch
        node, db, scanner, _ = make_node()
        node.register()
        node.ensure_job(3)
        node.passes = 1
        with patch.object(fn, "system_stats", return_value={"mem_avail_mb": 812, "mem_total_mb": 1900, "load1": 1.4}):
            node.tick()
        extra = db.store["scanner_job_checkpoints"][0]["extra"]
        self.assertEqual((extra["mem_avail_mb"], extra["mem_total_mb"], extra["load1"], extra["passes"]), (812, 1900, 1.4, 1))

    def test_system_stats_parses_proc_files(self) -> None:
        from unittest.mock import mock_open, patch

        files = {"/proc/meminfo": "MemTotal:        1945044 kB\nMemFree:  100 kB\nMemAvailable:    830000 kB\n", "/proc/loadavg": "1.52 1.10 0.90 1/200 1234\n"}
        with patch("builtins.open", side_effect=lambda path, *a, **k: mock_open(read_data=files[path])()):
            stats = fn.system_stats()
        self.assertEqual(stats, {"mem_avail_mb": 811, "mem_total_mb": 1899, "load1": 1.52})
        with patch("builtins.open", side_effect=OSError("no /proc")):
            self.assertEqual(fn.system_stats(), {})

    def test_a_resuming_node_staggers_briefly_instead_of_repeating_the_start_delay(self) -> None:
        sleeps: list[float] = []
        node, db, _, _ = make_node(lambda s: {}, sleep=sleeps.append, start_delay_sec=360)
        node.register()
        node.ensure_job(3)                                   # first start: creates the job
        fresh = make_node(lambda s: {}, sleep=sleeps.append, start_delay_sec=360)[0]
        fresh.db, fresh.ws = db, FakeWorkerState(db)
        fresh.run()                                          # restart: finds the job, short stagger only
        self.assertEqual(sleeps[0], fn.RESUME_STAGGER_SEC * 2)   # node index 2
        self.assertNotIn(360, sleeps)

    def test_segment_size_counts_unique_normalized_apns(self) -> None:
        node, _, _, _ = make_node(parcels=5)
        self.assertEqual(node._count_segment(), 5)


class S3ArtifactsTests(unittest.TestCase):
    def _artifacts(self, directory: Path, s3: MagicMock) -> fn.S3Artifacts:
        art = fn.S3Artifacts.__new__(fn.S3Artifacts)
        art._s3, art._bucket, art._prefix, art._dir = s3, "bucket", "runs/r1/node-3", directory
        return art

    def test_upload_sends_only_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "measw_cache.jsonl").write_text("x")
            s3 = MagicMock()
            self.assertEqual(self._artifacts(Path(tmp), s3).upload(), ["measw_cache.jsonl"])
            s3.upload_file.assert_called_once_with(str(Path(tmp) / "measw_cache.jsonl"), "bucket", "runs/r1/node-3/measw_cache.jsonl")

    def test_restore_skips_files_that_exist_and_tolerates_missing_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "vpt_removed.jsonl").write_text("local")
            s3 = MagicMock()
            s3.download_file.side_effect = Exception("404")
            self.assertEqual(self._artifacts(Path(tmp), s3).restore(), [])
            self.assertEqual(s3.download_file.call_count, 1)    # only the cache was missing locally


if __name__ == "__main__":
    unittest.main()
