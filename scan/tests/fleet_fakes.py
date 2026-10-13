"""In-memory stand-ins for the Supabase client and worker_state used by the fleet tests."""
from __future__ import annotations

import copy
import itertools
from pathlib import Path
from typing import Any, Callable

_ids = itertools.count(1)


def _get(row: dict, column: str) -> Any:
    if "->>" in column:
        base, key = column.split("->>", 1)
        return (row.get(base) or {}).get(key)
    return row.get(column)


class FakeQuery:
    def __init__(self, store: dict[str, list[dict]], table: str) -> None:
        self.store, self.table = store, table
        self.op, self.payload = "select", None
        self.filters: list[Callable[[dict], bool]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def select(self, *args, **kwargs):
        return self

    def update(self, payload: dict):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload: dict):
        self.op, self.payload = "insert", payload
        return self

    def eq(self, column: str, value: Any):
        self.filters.append(lambda row: _get(row, column) == value)
        return self

    def filter(self, column: str, operator: str, value: Any):
        assert operator == "eq", operator
        return self.eq(column, value)

    def in_(self, column: str, values: list):
        self.filters.append(lambda row: _get(row, column) in values)
        return self

    def order(self, column: str, desc: bool = False):
        self._order = (column, desc)
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": f"{self.table}-{next(_ids)}", **copy.deepcopy(self.payload)}
            rows.append(row)
            return type("R", (), {"data": [copy.deepcopy(row)]})()
        matched = [row for row in rows if all(f(row) for f in self.filters)]
        if self.op == "update":
            for row in matched:
                row.update(copy.deepcopy(self.payload))
        if self._order:
            column, desc = self._order
            matched.sort(key=lambda row: str(row.get(column) or ""), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return type("R", (), {"data": copy.deepcopy(matched)})()


class FakeClient:
    def __init__(self, store: dict[str, list[dict]] | None = None) -> None:
        self.store = store if store is not None else {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.store, name)


class FakeDb:
    def __init__(self, store: dict[str, list[dict]] | None = None) -> None:
        self.store = store if store is not None else {}
        self.client = FakeClient(self.store)

    def get_client(self) -> FakeClient:
        return self.client


class FakeWorkerState:
    """Same signatures as worker_state.py, backed by the in-memory store."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def heartbeat(self, name: str, tunnel_url: str | None = None) -> str:
        workers = self.db.store.setdefault("scanner_workers", [])
        for worker in workers:
            if worker["name"] == name:
                worker.update(status="online", last_heartbeat="now()")
                return worker["id"]
        worker = {"id": f"worker-{next(_ids)}", "name": name, "status": "online", "last_heartbeat": "now()"}
        workers.append(worker)
        return worker["id"]

    def create_job(self, worker_id: str, job_type: str, scope: dict) -> str:
        rows = self.db.client.table("scanner_jobs").insert(
            {"worker_id": worker_id, "job_type": job_type, "scope": scope, "status": "running",
             "processed_count": 0, "hit_count": 0, "started_at": "2026-10-09T00:00:00+00:00"}
        ).execute().data
        return rows[0]["id"]

    def update_job(self, job_id: str, **fields: Any) -> None:
        self.db.client.table("scanner_jobs").update(fields).eq("id", job_id).execute()

    def write_checkpoint(self, job_id, city, apn, row_offset, phase, extra=None) -> None:
        self.db.client.table("scanner_job_checkpoints").insert(
            {"job_id": job_id, "city": city, "apn": apn, "row_offset": row_offset, "phase": phase,
             "extra": extra, "created_at": f"2026-10-09T00:{len(self.db.store.get('scanner_job_checkpoints', [])):02d}:00+00:00"}
        ).execute()


class FakeScanner:
    """Stands in for find_meas_w_addresses for FleetNode tests."""

    def __init__(self, main_impl: Callable[["FakeScanner"], dict] | None = None, parcels: int = 3) -> None:
        self.scan_progress = {
            "scanned": 0, "total": parcels, "per_min": 0.0, "added": 0, "removed": 0, "unflagged": 0,
            "errors": 0, "rejections": 0, "paused_until": 0.0, "interval": 10.0, "current_apn": None,
        }
        self.main_impl = main_impl or (lambda scanner: {})
        self.main_calls: list[dict] = []
        self.stop_calls = 0
        self._parcels = parcels

    def request_stop(self) -> None:
        self.stop_calls += 1

    def get_input_csv_path(self, city: str | None = None) -> Path:
        return Path("oakland.csv")

    def _load_parcel_rows(self, path: Path, city: str | None = None):
        apns = [f"{i:03d} 000000{i}00" for i in range(1, self._parcels + 1)]
        return apns, {}

    def normalize_apn(self, apn: str) -> str:
        return apn.replace(" ", "")

    def main(self, city: str | None = None, restrict_to_csv: bool | None = None, **kwargs) -> dict:
        self.main_calls.append({"city": city, "restrict_to_csv": restrict_to_csv})
        return self.main_impl(self)
