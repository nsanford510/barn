#!/usr/bin/env python3
"""
Operator side of the vacancy research queue (runs on this machine, service key).

Remote agents never use this module: they call the Supabase RPCs from
db_migrations/supa_migration_vacancy_research.sql through
research_agent_kit/research_client.py with their own agent token.

    python research_queue.py add-agent grokbot-1      # prints the token once
    python research_queue.py revoke-agent grokbot-1
    python research_queue.py enqueue --city OAKLAND   # seed tasks, idempotent
    python research_queue.py requeue --status failed
    python research_queue.py status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

import db

TASKS = "vacancy_research_tasks"
FINDINGS = "vacancy_findings"
AGENTS = "research_agents"

TASK_STATUSES = ("pending", "claimed", "done", "failed")
VERDICTS = ("likely_vacant", "likely_occupied", "unknown")
PRIORITY_FAVORITE = 2
PRIORITY_POWER_OFF = 1
PRIORITY_DEFAULT = 0
_CHUNK = 500


def hash_token(token: str) -> str:
    """Must match _research_agent_name() in the migration: hex sha256 of the UTF-8 token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def add_agent(name: str) -> str:
    """Create the agent, or issue it a fresh token if it exists. Returns the token (not stored)."""
    name = name.strip()
    if not name:
        raise ValueError("agent name is required")
    token = secrets.token_urlsafe(32)
    db.get_client().table(AGENTS).upsert(
        {"name": name, "token_hash": hash_token(token), "active": True},
        on_conflict="name",
    ).execute()
    return token


def revoke_agent(name: str) -> bool:
    r = db.get_client().table(AGENTS).update({"active": False}).eq("name", name.strip()).execute()
    return bool(r.data)


def task_priority(bill: dict[str, Any], favorites: set[str]) -> int:
    if bill.get("apn") in favorites:
        return PRIORITY_FAVORITE
    if (bill.get("power_status") or "").lower() == "off":
        return PRIORITY_POWER_OFF
    return PRIORITY_DEFAULT


def build_task_rows(bills: list[dict[str, Any]], favorites: set[str], limit: int | None = None) -> list[dict[str, Any]]:
    """Task rows for the given bills, highest priority first, one per APN."""
    seen: set[str] = set()
    rows = []
    for bill in bills:
        apn = bill.get("apn")
        if not apn or apn in seen:
            continue
        seen.add(apn)
        rows.append({"apn": apn, "priority": task_priority(bill, favorites)})
    rows.sort(key=lambda row: (-row["priority"], row["apn"]))
    return rows if limit is None else rows[: max(0, limit)]


def enqueue(city: str | None = None, apns: list[str] | None = None, limit: int | None = None) -> int:
    """Add tasks for bills that have none yet. Existing tasks are left as they are.
    Returns the number of tasks added."""
    def _apply(q):
        if city:
            q = q.ilike("city", city)
        if apns:
            q = q.in_("apn", apns)
        return q

    bills = db._select_all("bills", "apn,power_status", apply=_apply)
    existing = {row["apn"] for row in db._select_all(TASKS, "apn")}
    rows = build_task_rows(
        [bill for bill in bills if bill.get("apn") not in existing],
        set(db.get_favorites_apns()),
        limit,
    )
    client = db.get_client()
    for start in range(0, len(rows), _CHUNK):
        client.table(TASKS).upsert(
            rows[start:start + _CHUNK], on_conflict="apn", ignore_duplicates=True
        ).execute()
    return len(rows)


def requeue(status: str = "failed", apns: list[str] | None = None) -> int:
    """Put failed (or done) tasks back in the queue with a fresh attempt count."""
    if status not in ("failed", "done"):
        raise ValueError("status must be 'failed' or 'done'")
    q = db.get_client().table(TASKS).update({
        "status": "pending",
        "attempts": 0,
        "claimed_by": None,
        "claim_token": None,
        "lease_expires_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("status", status)
    if apns:
        q = q.in_("apn", apns)
    return len(q.execute().data or [])


def _count(table: str, column: str, value: str) -> int:
    r = db.get_client().table(table).select("apn", count="exact", head=True).eq(column, value).execute()
    return r.count or 0


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def summarize(
    task_counts: dict[str, int],
    verdict_counts: dict[str, int],
    agents: list[dict[str, Any]],
    claimed: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Shape the queue status the CLI and the Scan page show."""
    now = now or datetime.now(timezone.utc)
    total = sum(task_counts.values())
    done = task_counts.get("done", 0)

    in_flight: dict[str, list[dict[str, Any]]] = {}
    expired = 0
    for task in claimed:
        lease = _parse(task.get("lease_expires_at"))
        is_expired = lease is not None and lease < now
        expired += is_expired
        in_flight.setdefault(task.get("claimed_by") or "", []).append({
            "apn": task.get("apn"),
            "lease_expires_at": task.get("lease_expires_at"),
            "expired": is_expired,
        })

    agent_rows = []
    for agent in agents:
        seen = _parse(agent.get("last_seen_at"))
        agent_rows.append({
            "name": agent.get("name"),
            "active": bool(agent.get("active")),
            "last_seen_at": agent.get("last_seen_at"),
            "last_seen_age_s": int((now - seen).total_seconds()) if seen else None,
            "claims": in_flight.get(agent.get("name") or "", []),
        })
    agent_rows.sort(key=lambda row: row["name"] or "")

    return {
        "tasks": {status: task_counts.get(status, 0) for status in TASK_STATUSES},
        "total": total,
        "pct": round(done * 100.0 / total, 1) if total else 0.0,
        "expired_leases": expired,
        "verdicts": {verdict: verdict_counts.get(verdict, 0) for verdict in VERDICTS},
        "agents": agent_rows,
    }


def status() -> dict[str, Any]:
    client = db.get_client()
    agents = client.table(AGENTS).select("name,active,last_seen_at").execute().data or []
    claimed = (
        client.table(TASKS).select("apn,claimed_by,lease_expires_at").eq("status", "claimed").execute().data or []
    )
    return summarize(
        {s: _count(TASKS, "status", s) for s in TASK_STATUSES},
        {v: _count("bills", "ai_vacancy_verdict", v) for v in VERDICTS},
        agents,
        claimed,
    )


def _clean_evidence(evidence: Any) -> list[dict[str, str]]:
    """Agent-written evidence, reduced to text fields; source_url is kept only if it is http(s)
    so the property page can link to it safely."""
    items = []
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        items.append({
            "signal": str(item.get("signal") or ""),
            "detail": str(item.get("detail") or ""),
            "observed_at": str(item.get("observed_at") or ""),
            "source_url": url if url.lower().startswith(("http://", "https://")) else "",
        })
    return items


def get_findings(apn: str, limit: int = 20) -> list[dict[str, Any]]:
    """Findings for one property, newest first."""
    r = (
        db.get_client()
        .table(FINDINGS)
        .select("id,agent,verdict,confidence,rationale,evidence,report_md,vacant_since,created_at")
        .eq("apn", apn)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    findings = r.data or []
    for finding in findings:
        finding["evidence"] = _clean_evidence(finding.get("evidence"))
        finding["report_md"] = str(finding.get("report_md") or "").strip()
    return findings


def _split(value: str | None) -> list[str] | None:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return items or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vacancy research queue (operator commands)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-agent", help="create an agent, or issue an existing one a new token")
    p.add_argument("name")

    p = sub.add_parser("revoke-agent", help="stop an agent's token from working")
    p.add_argument("name")

    p = sub.add_parser("enqueue", help="add tasks for bills that have none yet")
    p.add_argument("--city", default=None)
    p.add_argument("--apns", default=None, help="comma-separated APNs")
    p.add_argument("--limit", type=int, default=None, help="add at most this many (highest priority first)")

    p = sub.add_parser("requeue", help="put failed or done tasks back in the queue")
    p.add_argument("--status", choices=("failed", "done"), default="failed")
    p.add_argument("--apns", default=None, help="comma-separated APNs")

    sub.add_parser("status", help="queue counts, verdicts and per-agent activity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add-agent":
        token = add_agent(args.name)
        print(f"Agent: {args.name.strip()}")
        print(f"Token: {token}")
        print("Shown once and stored only as a hash. Set it as BARN_AGENT_TOKEN on the agent's machine.")
    elif args.command == "revoke-agent":
        if not revoke_agent(args.name):
            print(f"No agent named {args.name!r}", file=sys.stderr)
            return 1
        print(f"Revoked {args.name}")
    elif args.command == "enqueue":
        print(f"Added {enqueue(city=args.city, apns=_split(args.apns), limit=args.limit)} task(s)")
    elif args.command == "requeue":
        print(f"Requeued {requeue(status=args.status, apns=_split(args.apns))} task(s)")
    elif args.command == "status":
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
