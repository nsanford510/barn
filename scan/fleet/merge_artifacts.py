#!/usr/bin/env python3
"""
Merge the scan cache and removal log that fleet nodes backed up to S3 into this
computer's copies, so a later local scan sees what the fleet already verified
and the audit trail is complete.

    python fleet/merge_artifacts.py DOWNLOAD_DIR

DOWNLOAD_DIR holds node-1/, node-2/, ... each with measw_cache.jsonl and/or
vpt_removed.jsonl.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SCAN_DIR = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path) -> list[tuple[str, dict]]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((line, json.loads(line)))
            except json.JSONDecodeError:
                continue
    return rows


def merge_cache(local_path: Path, node_paths: list[Path]) -> int:
    """
    Append node cache entries that are newer than the local entry for the same
    APN (entries without checked_at lose to ones with it). The cache is
    append-only and the last line for an APN wins, so appending is enough.
    Returns the number of lines appended.
    """
    newest: dict[str, str] = {}
    for _, obj in _read_jsonl(local_path):
        apn = obj.get("apn")
        if apn:
            newest[apn] = max(newest.get(apn, ""), obj.get("checked_at") or "")
    appended: list[str] = []
    for path in node_paths:
        for line, obj in _read_jsonl(path):
            apn, stamp = obj.get("apn"), obj.get("checked_at") or ""
            if not apn:
                continue
            if apn not in newest or stamp > newest[apn]:
                newest[apn] = stamp
                appended.append(line)
    if appended:
        with local_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(appended) + "\n")
    return len(appended)


def merge_removed_log(local_path: Path, node_paths: list[Path]) -> int:
    """Append removal-log entries not already present. Returns the number appended."""
    seen = {(o.get("apn"), o.get("action"), o.get("at")) for _, o in _read_jsonl(local_path)}
    appended = []
    for path in node_paths:
        for line, obj in _read_jsonl(path):
            key = (obj.get("apn"), obj.get("action"), obj.get("at"))
            if key not in seen:
                seen.add(key)
                appended.append(line)
    if appended:
        with local_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(appended) + "\n")
    return len(appended)


def merge_all(download_dir: Path, scan_dir: Path = SCAN_DIR) -> dict:
    nodes = sorted(p for p in download_dir.glob("node-*") if p.is_dir())
    result = {"nodes": len(nodes)}
    for name, merge in (("measw_cache.jsonl", merge_cache), ("vpt_removed.jsonl", merge_removed_log)):
        local = scan_dir / name
        if local.exists():
            shutil.copyfile(local, local.with_name(local.stem + ".pre-merge.jsonl"))
        result[name] = merge(local, [n / name for n in nodes])
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(json.dumps(merge_all(Path(sys.argv[1]))))
