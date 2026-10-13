from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCAN_DIR = Path(__file__).resolve().parents[1]
for path in (SCAN_DIR, SCAN_DIR / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fleet_fakes import FakeClient
from webgui import fleet_status as fs

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)


def iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def job(i: int, status: str = "running", processed: int = 100, total: int = 1000, run_id: str = "run1", **kw) -> dict:
    return {
        "id": f"job{i}", "worker_id": f"w{i}", "job_type": fs.JOB_TYPE, "status": status,
        "scope": {"run_id": run_id, "segment": i, "of": 4, "total": total, "interval": 10},
        "processed_count": processed, "hit_count": 0, "current_apn": f"00{i} 000000100",
        "started_at": iso(3600), "error_summary": None, **kw,
    }


def worker(i: int, heartbeat_ago: float = 10) -> dict:
    return {"id": f"w{i}", "name": f"barn-scan-node-{i}", "last_heartbeat": iso(heartbeat_ago),
            "capabilities": {"instance_id": f"i-{i}", "public_ip": f"1.2.3.{i}", "az": "us-west-2a"}}


def checkpoints(offsets_and_ago: list[tuple[int, float]], **extra) -> list[dict]:
    return [{"row_offset": offset, "created_at": iso(ago), "extra": {"per_min": 5.5, "total": 1000, **extra}}
            for offset, ago in offsets_and_ago]


class BuildStatusTests(unittest.TestCase):
    def test_no_run(self) -> None:
        status = fs.build_status([], {}, {}, NOW)
        self.assertEqual((status["run_id"], status["nodes"], status["overall"]), (None, [], None))

    def test_running_node_speed_comes_from_checkpoint_history(self) -> None:
        cps = checkpoints([(100, 0), (90, 100), (80, 200), (70, 300)])  # 10 parcels / 100 s = 6/min
        node = fs.build_node(job(1), worker(1), cps, NOW)

        self.assertEqual(node["state"], "running")
        self.assertEqual(node["speed_now"], 6.0)
        self.assertEqual(node["speed_avg"], 5.5)
        self.assertEqual((node["scanned"], node["total"], node["pct"]), (100, 1000, 10.0))
        self.assertEqual((node["public_ip"], node["instance_id"], node["segment"]), ("1.2.3.1", "i-1", 1))

    def test_speed_window_ignores_old_checkpoints(self) -> None:
        # The 3 newest checkpoints are 6/min; the one 30 minutes back must not drag the average.
        cps = checkpoints([(100, 0), (90, 100), (80, 200), (10, 1800)])
        self.assertEqual(fs._speed_now(cps), 6.0)

    def test_node_exposes_memory_load_and_retry_pass(self) -> None:
        cps = checkpoints([(10, 0)], mem_avail_mb=640, mem_total_mb=1900, load1=1.8, passes=2)
        node = fs.build_node(job(1), worker(1), cps, NOW)
        self.assertEqual((node["mem_avail_mb"], node["mem_total_mb"], node["load1"], node["passes"]), (640, 1900, 1.8, 2))
        bare = fs.build_node(job(1), worker(1), [], NOW)
        self.assertEqual((bare["mem_avail_mb"], bare["load1"], bare["passes"]), (None, None, 0))

    def test_checkpoints_too_close_together_do_not_produce_a_speed(self) -> None:
        self.assertIsNone(fs._speed_now(checkpoints([(100, 0), (95, 0.1)])))
        node = fs.build_node(job(1), worker(1), checkpoints([(100, 0), (95, 0.1)]), NOW)
        self.assertEqual(node["speed_now"], 5.5)   # falls back to the node's own average

    def test_speed_falls_back_to_reported_average_with_one_checkpoint(self) -> None:
        node = fs.build_node(job(1), worker(1), checkpoints([(50, 5)]), NOW)
        self.assertEqual(node["speed_now"], 5.5)

    def test_state_classification(self) -> None:
        self.assertEqual(fs.build_node(job(1), worker(1, heartbeat_ago=200), [], NOW)["state"], "stale")
        self.assertEqual(fs.build_node(job(1), None, [], NOW)["state"], "stale")
        self.assertEqual(fs.build_node(job(1, "interrupted"), worker(1), [], NOW)["state"], "stopped")
        self.assertEqual(fs.build_node(job(1, "completed"), worker(1), [], NOW)["state"], "completed")
        failed = fs.build_node(job(1, "failed", error_summary="blocked"), worker(1), [], NOW)
        self.assertEqual((failed["state"], failed["error_summary"]), ("failed", "blocked"))

        paused_until = NOW.timestamp() + 45
        paused = fs.build_node(job(1), worker(1), checkpoints([(5, 0)], paused_until=paused_until, rejections=2), NOW)
        self.assertEqual((paused["state"], paused["rejections"], paused["paused_until"]), ("paused", 2, paused_until))
        # A pause that already ended no longer shows.
        over = fs.build_node(job(1), worker(1), checkpoints([(5, 0)], paused_until=NOW.timestamp() - 5), NOW)
        self.assertEqual((over["state"], over["paused_until"]), ("running", None))

    def test_non_running_nodes_report_zero_speed(self) -> None:
        cps = checkpoints([(100, 0), (90, 100)])
        for status in ("interrupted", "failed", "completed"):
            self.assertEqual(fs.build_node(job(1, status), worker(1), cps, NOW)["speed_now"], 0.0)
        self.assertEqual(fs.build_node(job(1), worker(1, heartbeat_ago=500), cps, NOW)["speed_now"], 0.0)

    def test_completed_node_shows_full_progress(self) -> None:
        node = fs.build_node(job(1, "completed", processed=997), worker(1), [], NOW)
        self.assertEqual((node["scanned"], node["pct"]), (1000, 100.0))

    def test_overall_sums_and_eta(self) -> None:
        jobs = [job(i, processed=100 * i) for i in (1, 2, 3, 4)]
        workers = {f"w{i}": worker(i) for i in (1, 2, 3, 4)}
        cps = {f"job{i}": checkpoints([(100 * i, 0), (100 * i - 10, 100)], added=i, removed=2 * i) for i in (1, 2, 3, 4)}
        status = fs.build_status(jobs, workers, cps, NOW)
        overall = status["overall"]

        self.assertEqual([n["segment"] for n in status["nodes"]], [1, 2, 3, 4])
        self.assertEqual((overall["scanned"], overall["total"], overall["pct"]), (1000, 4000, 25.0))
        self.assertEqual(overall["speed_now"], 24.0)                      # 4 nodes x 6/min
        self.assertEqual(overall["eta_seconds"], round(3000 * 60 / 24))    # remaining / speed
        self.assertEqual((overall["added"], overall["removed"]), (10, 20))
        self.assertEqual(overall["states"], {"running": 4})
        self.assertEqual(status["run_id"], "run1")

    def test_eta_is_unknown_when_nothing_is_moving_and_zero_when_done(self) -> None:
        stopped = fs.build_status([job(1, "interrupted")], {"w1": worker(1)}, {}, NOW)["overall"]
        self.assertIsNone(stopped["eta_seconds"])
        done = fs.build_status([job(1, "completed", processed=1000)], {"w1": worker(1)}, {}, NOW)["overall"]
        self.assertEqual(done["eta_seconds"], 0)


class FetchStatusTests(unittest.TestCase):
    def _store(self) -> dict:
        return {
            "scanner_workers": [worker(1), worker(2)],
            "scanner_jobs": [
                job(1, run_id="old", started_at=iso(90000)),
                job(1, run_id="run2", started_at=iso(100)),
                job(2, run_id="run2", started_at=iso(100)),
            ],
            "scanner_job_checkpoints": [
                {"job_id": "job2", **cp} for cp in checkpoints([(10, 0), (5, 60)])
            ],
        }

    def test_latest_run_is_selected_by_default(self) -> None:
        status = fs.fetch_fleet_status(FakeClient(self._store()), now=NOW)
        self.assertEqual(status["run_id"], "run2")
        self.assertEqual(len(status["nodes"]), 2)

    def test_run_id_can_be_requested(self) -> None:
        status = fs.fetch_fleet_status(FakeClient(self._store()), run_id="old", now=NOW)
        self.assertEqual((status["run_id"], len(status["nodes"])), ("old", 1))

    def test_empty_database(self) -> None:
        status = fs.fetch_fleet_status(FakeClient({}), now=NOW)
        self.assertIsNone(status["overall"])

    def test_stop_and_resume_only_touch_the_latest_run(self) -> None:
        store = self._store()
        store["scanner_jobs"][2]["status"] = "failed"
        client = FakeClient(store)

        self.assertEqual(fs.set_fleet_state(client, "stop"), {"run_id": "run2", "changed": 1})
        statuses = {(j["scope"]["run_id"], j["id"]): j["status"] for j in store["scanner_jobs"]}
        self.assertEqual(statuses[("run2", "job1")], "interrupted")
        self.assertEqual(statuses[("run2", "job2")], "failed")       # not running, so left alone
        self.assertEqual(statuses[("old", "job1")], "running")       # other run untouched

        store["scanner_jobs"][2]["error_summary"] = "blocked"
        self.assertEqual(fs.set_fleet_state(client, "resume"), {"run_id": "run2", "changed": 2})
        self.assertEqual([j["status"] for j in store["scanner_jobs"] if j["scope"]["run_id"] == "run2"], ["running", "running"])
        self.assertIsNone(store["scanner_jobs"][2]["error_summary"])

    def test_invalid_action(self) -> None:
        with self.assertRaises(ValueError):
            fs.set_fleet_state(FakeClient({}), "explode")


if __name__ == "__main__":
    unittest.main()
