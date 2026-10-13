from __future__ import annotations

"""
Worker state management for BARN scan workers.

Provides heartbeat, job tracking, checkpoints, and discovery marking
backed by Supabase tables:
  - scanner_workers
  - scanner_jobs
  - scanner_job_checkpoints
  - bills (mark_discovery)
"""

import db

# Sentinel to distinguish "not provided" from "explicitly None"
_UNSET = object()


def heartbeat(worker_name: str, tunnel_url: str | None = None) -> str:
    """Registers the worker on first call and refreshes its heartbeat after that.
    Returns worker_id (uuid str).

    scanner_workers has no unique constraint on name, so the row is looked up
    rather than upserted with on_conflict="name" (which Postgres rejects)."""
    client = db.get_client()
    payload: dict = {
        "last_heartbeat": "now()",
        "status": "online",
    }
    if tunnel_url is not None:
        payload["tunnel_url"] = tunnel_url

    existing = (
        client.table("scanner_workers")
        .select("id")
        .eq("name", worker_name)
        .order("created_at")
        .limit(1)
        .execute()
    )
    if existing.data:
        worker_id = existing.data[0]["id"]
        client.table("scanner_workers").update(payload).eq("id", worker_id).execute()
        return worker_id

    result = client.table("scanner_workers").insert({"name": worker_name, **payload}).execute()
    if not result.data:
        raise RuntimeError(f"heartbeat failed: insert returned empty response for worker {worker_name}")
    return result.data[0]["id"]


def create_job(worker_id: str, job_type: str, scope: dict) -> str:
    """Inserts scanner_jobs row with status='running'. Returns job_id (uuid str)."""
    client = db.get_client()
    result = (
        client.table("scanner_jobs")
        .insert(
            {
                "worker_id": worker_id,
                "job_type": job_type,
                "scope": scope,
                "status": "running",
            }
        )
        .execute()
    )
    if not result.data:
        raise RuntimeError(f"create_job failed: insert returned empty response for job_type {job_type}")
    return result.data[0]["id"]


def update_job(
    job_id: str,
    *,
    status: str | type(_UNSET) = _UNSET,
    current_city: str | None | type(_UNSET) = _UNSET,
    current_apn: str | None | type(_UNSET) = _UNSET,
    processed_count: int | None | type(_UNSET) = _UNSET,
    hit_count: int | None | type(_UNSET) = _UNSET,
    error_summary: str | None | type(_UNSET) = _UNSET,
) -> None:
    """Patches scanner_jobs row. Only updates provided kwargs.
    Use None to explicitly clear a field, omit the parameter to leave it unchanged."""
    patch: dict = {}
    if status is not _UNSET:
        patch["status"] = status
    if current_city is not _UNSET:
        patch["current_city"] = current_city
    if current_apn is not _UNSET:
        patch["current_apn"] = current_apn
    if processed_count is not _UNSET:
        patch["processed_count"] = processed_count
    if hit_count is not _UNSET:
        patch["hit_count"] = hit_count
    if error_summary is not _UNSET:
        patch["error_summary"] = error_summary

    if not patch:
        return

    client = db.get_client()
    client.table("scanner_jobs").update(patch).eq("id", job_id).execute()


def write_checkpoint(
    job_id: str,
    city: str | None,
    apn: str | None,
    row_offset: int | None,
    phase: str | None,
    extra: dict | None = None,
) -> None:
    """Inserts a scanner_job_checkpoints row."""
    client = db.get_client()
    payload: dict = {"job_id": job_id}
    if city is not None:
        payload["city"] = city
    if apn is not None:
        payload["apn"] = apn
    if row_offset is not None:
        payload["row_offset"] = row_offset
    if phase is not None:
        payload["phase"] = phase
    if extra is not None:
        payload["extra"] = extra

    client.table("scanner_job_checkpoints").insert(payload).execute()


def get_latest_checkpoint(job_id: str) -> dict | None:
    """Returns most recent checkpoint for a job (ordered by created_at DESC), or None."""
    client = db.get_client()
    result = (
        client.table("scanner_job_checkpoints")
        .select("*")
        .eq("job_id", job_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def get_resumable_job(job_type: str) -> dict | None:
    """Returns most recent scanner_jobs row with status='interrupted' for the given job_type, or None.
    Includes the latest checkpoint for that job in a 'checkpoint' key."""
    client = db.get_client()
    result = (
        client.table("scanner_jobs")
        .select("*")
        .eq("status", "interrupted")
        .eq("job_type", job_type)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None

    job = dict(result.data[0])
    job["checkpoint"] = get_latest_checkpoint(job["id"])
    return job


def mark_discovery(apn: str, job_id: str) -> bool:
    """Sets first_seen_at=now() and discovered_by_job_id=job_id on bills row ONLY IF first_seen_at IS NULL.
    Returns True if this was a new discovery (row was updated), False if already had first_seen_at."""
    client = db.get_client()
    result = (
        client.table("bills")
        .update({"first_seen_at": "now()", "discovered_by_job_id": job_id})
        .eq("apn", apn)
        .is_("first_seen_at", "null")
        .execute()
    )
    return len(result.data) > 0
