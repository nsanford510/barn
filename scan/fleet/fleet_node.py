#!/usr/bin/env python3
"""
One node of the scan fleet.

Runs the VPT scan over this node's segment of a city (the node's `oakland.csv`
is its segment file), reports progress to Supabase so the web UI can show it,
and backs the scan cache up to S3 so a replaced node can resume.

Progress is reported through the existing worker tables (worker_state.py):
  scanner_workers            heartbeat + instance info
  scanner_jobs               one 'fleet_scan' job per node and run
  scanner_job_checkpoints    periodic snapshots (row_offset = parcels done,
                             extra = speed and counters)

Setting the job's status to 'interrupted' (Stop fleet in the web UI) pauses the
node; setting it back to 'running' resumes it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

JOB_TYPE = "fleet_scan"
HEARTBEAT_SEC = 30
REPORT_SEC = 15
CHECKPOINT_SEC = 100
UPLOAD_SEC = 600
IDLE_POLL_SEC = 15
MAX_RETRY_PASSES = 3        # extra passes over parcels whose lookup failed (they are never cached)
RETRY_PAUSE_SEC = 60
RESUME_STAGGER_SEC = 15     # per node index, instead of the full start delay, when resuming a job
ARTIFACT_FILES = ("measw_cache.jsonl", "vpt_removed.jsonl")


def system_stats() -> dict:
    """Free memory and load on this machine (Linux); {} where /proc is unavailable."""
    out: dict[str, Any] = {}
    try:
        with open("/proc/meminfo") as f:
            info = {line.split(":")[0]: int(line.split()[1]) for line in f if ":" in line}
        out["mem_avail_mb"] = round(info["MemAvailable"] / 1024)
        out["mem_total_mb"] = round(info["MemTotal"] / 1024)
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            out["load1"] = float(f.read().split()[0])
    except Exception:
        pass
    return out


class NullArtifacts:
    """No backup configured: nothing to restore or upload."""

    def restore(self) -> list[str]:
        return []

    def upload(self) -> list[str]:
        return []


class S3Artifacts:
    """Backs the node's cache and removal log up to s3://bucket/prefix/node-<i>/."""

    def __init__(self, bucket: str, prefix: str, node_index: int, directory: Path = SCAN_DIR) -> None:
        import boto3

        self._s3 = boto3.client("s3")
        self._bucket = bucket
        self._prefix = f"{prefix.strip('/')}/node-{node_index}"
        self._dir = directory

    def restore(self) -> list[str]:
        restored = []
        for name in ARTIFACT_FILES:
            target = self._dir / name
            if target.exists():
                continue
            try:
                self._s3.download_file(self._bucket, f"{self._prefix}/{name}", str(target))
                restored.append(name)
            except Exception:
                continue  # nothing backed up yet
        return restored

    def upload(self) -> list[str]:
        uploaded = []
        for name in ARTIFACT_FILES:
            source = self._dir / name
            if source.exists():
                self._s3.upload_file(str(source), self._bucket, f"{self._prefix}/{name}")
                uploaded.append(name)
        return uploaded


class FleetNode:
    def __init__(
        self,
        *,
        run_id: str,
        index: int,
        count: int,
        scanner: Any,
        worker_state: Any,
        db: Any,
        artifacts: Any = None,
        city: str = "OAKLAND",
        info: dict | None = None,
        start_delay_sec: float = 0,
        shutdown_when_done: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        shutdown: Callable[[], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.index = index
        self.count = count
        self.scanner = scanner
        self.ws = worker_state
        self.db = db
        self.artifacts = artifacts or NullArtifacts()
        self.city = city
        self.info = info or {}
        self.start_delay_sec = start_delay_sec
        self.shutdown_when_done = shutdown_when_done
        self._sleep = sleep
        self._clock = clock
        self._shutdown = shutdown or (lambda: subprocess.run(["shutdown", "-h", "now"], check=False))
        self.name = f"barn-scan-node-{index}"
        self.worker_id: str | None = None
        self.job_id: str | None = None
        self.total = 0
        self.scanning = False
        self.ui_stop = False
        self.passes = 0
        self.resumed = False
        self._heartbeat_stop = threading.Event()
        self._report_stop = threading.Event()
        self._last_checkpoint = float("-inf")
        self._last_upload = self._clock()

    # -- registration -------------------------------------------------------

    def register(self) -> str:
        self.worker_id = self.ws.heartbeat(self.name)
        capabilities = {**self.info, "run_id": self.run_id, "segment": self.index, "of": self.count}
        self.db.get_client().table("scanner_workers").update({"capabilities": capabilities}).eq("id", self.worker_id).execute()
        return self.worker_id

    def ensure_job(self, total: int) -> str:
        """Reuse this node's job for the run (after a restart), else create one."""
        self.total = total
        existing = (
            self.db.get_client().table("scanner_jobs").select("id,status")
            .eq("worker_id", self.worker_id).eq("job_type", JOB_TYPE)
            .filter("scope->>run_id", "eq", self.run_id)
            .order("started_at", desc=True).limit(1).execute().data
        )
        if existing:
            self.job_id = existing[0]["id"]
            self.resumed = True
            return self.job_id
        scope = {
            "run_id": self.run_id, "segment": self.index, "of": self.count, "total": total,
            "interval": float(os.getenv("VPT_LOOKUP_INTERVAL_SEC", "10") or 10),
        }
        self.job_id = self.ws.create_job(self.worker_id, JOB_TYPE, scope)
        return self.job_id

    # -- background threads -------------------------------------------------

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(HEARTBEAT_SEC):
            try:
                self.ws.heartbeat(self.name)
            except Exception as exc:  # transient Supabase/network error: keep going
                print(f"heartbeat failed: {exc}", flush=True)

    def _report_loop(self) -> None:
        while not self._report_stop.wait(REPORT_SEC):
            self.tick()

    def job_status(self) -> str | None:
        rows = self.db.get_client().table("scanner_jobs").select("status").eq("id", self.job_id).limit(1).execute().data
        return rows[0]["status"] if rows else None

    def tick(self) -> None:
        """One reporting pass: progress, periodic checkpoint/backup, and the stop signal."""
        try:
            snap = dict(self.scanner.scan_progress)
            if not snap.get("total"):
                return  # the scan has not started (start delay): report nothing rather than a false zero
            update: dict[str, Any] = {
                "processed_count": snap.get("scanned", 0),
                "hit_count": snap.get("added", 0),
                "current_city": self.city,
            }
            if snap.get("current_apn"):
                update["current_apn"] = snap["current_apn"]
            self.ws.update_job(self.job_id, **update)

            now = self._clock()
            if now - self._last_checkpoint >= CHECKPOINT_SEC:
                self.ws.write_checkpoint(
                    self.job_id, self.city, snap.get("current_apn"), snap.get("scanned", 0), "scan",
                    extra={
                        **{k: snap.get(k) for k in (
                            "total", "per_min", "added", "removed", "unflagged", "errors",
                            "rejections", "paused_until", "interval",
                        )},
                        "passes": self.passes,
                        **system_stats(),
                    },
                )
                self._last_checkpoint = now
            if now - self._last_upload >= UPLOAD_SEC:
                self.artifacts.upload()
                self._last_upload = now

            if self.scanning and self.job_status() == "interrupted":
                self.ui_stop = True
                self.scanner.request_stop()
        except Exception as exc:
            print(f"progress report failed: {exc}", flush=True)

    # -- main flow ------------------------------------------------------------

    def _count_segment(self) -> int:
        csv_path = self.scanner.get_input_csv_path(city=self.city)
        apns, _ = self.scanner._load_parcel_rows(csv_path, self.city)
        return len({self.scanner.normalize_apn(a) for a in apns})

    def _set_status(self, status: str, error: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if error is not None:
            fields["error_summary"] = error
        self.ws.update_job(self.job_id, **fields)

    def _finish(self) -> None:
        self._report_stop.set()
        self._heartbeat_stop.set()
        self.tick()
        try:
            self.artifacts.upload()
        except Exception as exc:
            print(f"final backup failed: {exc}", flush=True)
        self.db.get_client().table("scanner_workers").update({"status": "offline"}).eq("id", self.worker_id).execute()

    def run(self) -> int:
        self.register()
        self.ensure_job(self._count_segment())
        print(f"{self.name}: run {self.run_id}, job {self.job_id}, {self.total} parcels", flush=True)
        try:
            restored = self.artifacts.restore()
            if restored:
                print(f"restored from backup: {restored}", flush=True)
        except Exception as exc:
            print(f"restore failed: {exc}", flush=True)

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._report_loop, daemon=True).start()
        # A fresh node waits out its start delay so the nodes are staggered; a node resuming
        # after a restart already has progress, so it only needs a short stagger.
        self._sleep(self.index * RESUME_STAGGER_SEC if self.resumed else self.start_delay_sec)

        while True:
            status = self.job_status()
            if status == "completed":
                self._finish()
                if self.shutdown_when_done:
                    self._shutdown()
                return 0
            if status != "running":  # interrupted from the UI, or failed: wait for Resume
                self._sleep(IDLE_POLL_SEC)
                continue

            self.ui_stop = False
            self.scanning = True
            try:
                stats = self.scanner.main(city=self.city, restrict_to_csv=True) or {}
            except Exception as exc:
                self.scanning = False
                try:
                    self._set_status("running", error=f"crashed: {exc!r}"[:500])
                except Exception:
                    pass
                raise  # process exits non-zero; systemd restarts it and the cache resumes the scan
            finally:
                self.scanning = False

            if self.ui_stop:
                continue  # paused from the UI; the loop idles until Resume
            if stats.get("aborted"):
                self._set_status("failed", error="blocked: 5 consecutive rejections from the tax site")
                self.tick()
                continue
            failed = int(stats.get("error", 0) or 0)
            if failed and self.passes < MAX_RETRY_PASSES:
                # Failed lookups are never cached, so another pass picks up exactly those parcels.
                self.passes += 1
                print(f"pass {self.passes}: {failed} lookups failed; retrying them in {RETRY_PAUSE_SEC}s", flush=True)
                self._sleep(RETRY_PAUSE_SEC)
                continue
            self._set_status(
                "completed",
                error=f"{failed} lookups still failing after {self.passes} retry passes; rerun to retry them" if failed else None,
            )
            self._finish()
            if self.shutdown_when_done:
                self._shutdown()
            return 0


def node_from_env() -> FleetNode:
    import db
    import find_meas_w_addresses as scanner
    import worker_state

    index = int(os.environ["FLEET_NODE_INDEX"])
    bucket = os.getenv("FLEET_S3_BUCKET", "").strip()
    prefix = os.getenv("FLEET_S3_PREFIX", "").strip()
    artifacts = S3Artifacts(bucket, prefix, index) if bucket else NullArtifacts()
    return FleetNode(
        run_id=os.environ["FLEET_RUN_ID"],
        index=index,
        count=int(os.getenv("FLEET_NODE_COUNT", "4")),
        scanner=scanner,
        worker_state=worker_state,
        db=db,
        artifacts=artifacts,
        city=os.getenv("FLEET_CITY", "OAKLAND").upper(),
        info={
            "instance_id": os.getenv("FLEET_INSTANCE_ID"),
            "public_ip": os.getenv("FLEET_PUBLIC_IP"),
            "az": os.getenv("FLEET_AZ"),
        },
        start_delay_sec=float(os.getenv("FLEET_START_DELAY_SEC", "0") or 0),
        shutdown_when_done=os.getenv("FLEET_SHUTDOWN_WHEN_DONE", "").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    raise SystemExit(node_from_env().run())
