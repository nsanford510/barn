"""
Fleet status for the web UI: builds per-node and overall progress/speed from the
worker tables the fleet nodes write to (scanner_workers, scanner_jobs,
scanner_job_checkpoints). Pure functions plus thin Supabase fetch helpers.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

JOB_TYPE = "fleet_scan"
STALE_AFTER_SEC = 90          # no heartbeat for this long while the job is 'running'
SPEED_WINDOW_SEC = 600        # "speed now" is measured over the last 10 minutes of checkpoints
MIN_SPEED_SPAN_SEC = 30       # checkpoints closer together than this say nothing about speed
CHECKPOINTS_PER_JOB = 10
COUNTERS = ("added", "removed", "unflagged", "errors", "rejections")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _speed_now(checkpoints: list[dict]) -> float | None:
    """Parcels/min between the newest checkpoint and the oldest one inside the window."""
    points = []
    for cp in checkpoints:
        created = _parse(cp.get("created_at"))
        if created is not None and cp.get("row_offset") is not None:
            points.append((created, cp["row_offset"]))
    if len(points) < 2:
        return None
    points.sort(reverse=True)  # newest first
    newest_at, newest_offset = points[0]
    oldest_at, oldest_offset = newest_at, newest_offset
    for at, offset in points[1:]:
        if (newest_at - at).total_seconds() > SPEED_WINDOW_SEC:
            break
        oldest_at, oldest_offset = at, offset
    seconds = (newest_at - oldest_at).total_seconds()
    if seconds < MIN_SPEED_SPAN_SEC:
        return None
    return max(0.0, (newest_offset - oldest_offset) * 60.0 / seconds)


def _node_state(job: dict, heartbeat_age: float | None, paused_until: float | None, now: datetime) -> str:
    status = job.get("status")
    if status in ("completed", "failed"):
        return status
    if status == "interrupted":
        return "stopped"
    if heartbeat_age is None or heartbeat_age > STALE_AFTER_SEC:
        return "stale"
    if paused_until and paused_until > now.timestamp():
        return "paused"
    return "running"


def build_node(job: dict, worker: dict | None, checkpoints: list[dict], now: datetime) -> dict:
    scope = job.get("scope") or {}
    checkpoints = sorted(checkpoints, key=lambda cp: cp.get("created_at") or "", reverse=True)
    extra = (checkpoints[0].get("extra") if checkpoints else None) or {}
    capabilities = (worker or {}).get("capabilities") or {}

    heartbeat = _parse((worker or {}).get("last_heartbeat"))
    heartbeat_age = (now - heartbeat).total_seconds() if heartbeat else None
    paused_until = extra.get("paused_until") or 0
    state = _node_state(job, heartbeat_age, paused_until, now)

    total = scope.get("total") or extra.get("total") or 0
    scanned = job.get("processed_count") or 0
    if state == "completed" and total:
        scanned = max(scanned, total)
    speed_avg = extra.get("per_min")
    speed_now = _speed_now(checkpoints)
    if speed_now is None:
        speed_now = speed_avg
    if state not in ("running", "paused"):
        speed_now = 0.0

    return {
        "segment": scope.get("segment"),
        "name": (worker or {}).get("name"),
        "job_id": job.get("id"),
        "state": state,
        "status": job.get("status"),
        "scanned": scanned,
        "total": total,
        "pct": round(min(100.0, scanned * 100.0 / total), 1) if total else 0.0,
        "speed_now": round(speed_now or 0.0, 1),
        "speed_avg": round(speed_avg or 0.0, 1),
        **{key: extra.get(key) or 0 for key in COUNTERS},
        "interval": extra.get("interval") or scope.get("interval"),
        "paused_until": paused_until if paused_until and paused_until > now.timestamp() else None,
        "current_apn": job.get("current_apn"),
        "mem_avail_mb": extra.get("mem_avail_mb"),
        "mem_total_mb": extra.get("mem_total_mb"),
        "load1": extra.get("load1"),
        "passes": extra.get("passes") or 0,
        "instance_id": capabilities.get("instance_id"),
        "public_ip": capabilities.get("public_ip"),
        "az": capabilities.get("az"),
        "heartbeat_age_s": round(heartbeat_age) if heartbeat_age is not None else None,
        "started_at": job.get("started_at"),
        "error_summary": job.get("error_summary"),
    }


def build_status(
    jobs: list[dict],
    workers: dict[str, dict],
    checkpoints: dict[str, list[dict]],
    now: datetime | None = None,
) -> dict:
    """jobs: the run's fleet_scan jobs; workers: by id; checkpoints: by job id."""
    now = now or datetime.now(timezone.utc)
    if not jobs:
        return {"run_id": None, "nodes": [], "overall": None, "updated_at": now.isoformat()}

    nodes = [
        build_node(job, workers.get(job.get("worker_id")), checkpoints.get(job.get("id"), []), now)
        for job in jobs
    ]
    nodes.sort(key=lambda node: (node["segment"] is None, node["segment"] or 0))

    scanned = sum(node["scanned"] for node in nodes)
    total = sum(node["total"] for node in nodes)
    speed = round(sum(node["speed_now"] for node in nodes), 1)
    remaining = max(total - scanned, 0)
    started = [parsed for parsed in (_parse(node["started_at"]) for node in nodes) if parsed]
    overall = {
        "scanned": scanned,
        "total": total,
        "pct": round(min(100.0, scanned * 100.0 / total), 1) if total else 0.0,
        "speed_now": speed,
        "speed_avg": round(sum(node["speed_avg"] for node in nodes if node["state"] in ("running", "paused")), 1),
        "eta_seconds": round(remaining * 60.0 / speed) if speed > 0 and remaining else (0 if not remaining else None),
        "states": dict(Counter(node["state"] for node in nodes)),
        "nodes": len(nodes),
        "started_at": min(started).isoformat() if started else None,
        **{key: sum(node[key] for node in nodes) for key in COUNTERS},
    }
    return {
        "run_id": (jobs[0].get("scope") or {}).get("run_id"),
        "nodes": nodes,
        "overall": overall,
        "updated_at": now.isoformat(),
    }


def _run_jobs(client: Any, run_id: str | None) -> tuple[str | None, list[dict]]:
    if run_id is None:
        latest = (
            client.table("scanner_jobs").select("id,scope").eq("job_type", JOB_TYPE)
            .order("started_at", desc=True).limit(1).execute().data
        )
        if not latest:
            return None, []
        run_id = (latest[0].get("scope") or {}).get("run_id")
        if not run_id:
            return None, []
    jobs = (
        client.table("scanner_jobs").select("*").eq("job_type", JOB_TYPE)
        .filter("scope->>run_id", "eq", run_id).execute().data
    ) or []
    return run_id, jobs


def fetch_fleet_status(client: Any, run_id: str | None = None, now: datetime | None = None) -> dict:
    run_id, jobs = _run_jobs(client, run_id)
    if not jobs:
        return build_status([], {}, {}, now)
    worker_ids = [job["worker_id"] for job in jobs if job.get("worker_id")]
    workers = {
        w["id"]: w
        for w in (client.table("scanner_workers").select("*").in_("id", worker_ids).execute().data or [])
    }
    checkpoints = {
        job["id"]: (
            client.table("scanner_job_checkpoints").select("row_offset,extra,created_at")
            .eq("job_id", job["id"]).order("created_at", desc=True).limit(CHECKPOINTS_PER_JOB).execute().data
        ) or []
        for job in jobs
    }
    return build_status(jobs, workers, checkpoints, now)


def set_fleet_state(client: Any, action: str, run_id: str | None = None) -> dict:
    """
    'stop' pauses the run's running jobs (nodes finish their current lookup and
    idle); 'resume' restarts stopped or failed ones. Returns the run id and the
    number of jobs changed.
    """
    if action not in ("stop", "resume"):
        raise ValueError("action must be 'stop' or 'resume'")
    run_id, jobs = _run_jobs(client, run_id)
    if not jobs:
        return {"run_id": None, "changed": 0}
    if action == "stop":
        from_states, patch = ("running",), {"status": "interrupted"}
    else:
        from_states, patch = ("interrupted", "failed"), {"status": "running", "error_summary": None}
    changed = (
        client.table("scanner_jobs").update(patch).eq("job_type", JOB_TYPE)
        .filter("scope->>run_id", "eq", run_id).in_("status", list(from_states)).execute().data
    ) or []
    return {"run_id": run_id, "changed": len(changed)}
