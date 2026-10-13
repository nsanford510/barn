#!/usr/bin/env python3
"""
Split a city's parcels into N disjoint segments, one per fleet node.

Parcels whose latest bill was already verified this bill year (fresh in the
local scan cache) are left out. Properties already in the DB are dealt out
first, VPT payers before delinquent-only, so every node re-checks an equal
share of them before it starts discovering new parcels.

    python fleet/split_parcels.py --city OAKLAND --nodes 4
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

import find_meas_w_addresses as scanner  # noqa: E402

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"


def read_city_parcels(csv_path: Path, city: str) -> tuple[list[str], list[tuple[str, dict]]]:
    """Fieldnames plus (normalized APN, row) for the first row of each APN in the city."""
    city = city.strip().upper()
    parcels: list[tuple[str, dict]] = []
    seen: set[str] = set()
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            row_city = (row.get("CITY") or row.get("SitusCity") or "").strip().upper()
            if row_city and row_city != city:
                continue
            apn = (row.get("APN") or "").strip()
            norm = scanner.normalize_apn(apn)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            parcels.append((norm, row))
    return fieldnames, parcels


def fresh_normalized_apns(cache: dict[str, dict]) -> set[str]:
    return {scanner.normalize_apn(apn) for apn, entry in cache.items() if scanner.is_cache_fresh(entry)}


def split_parcels(
    parcels: list[tuple[str, dict]],
    owned: Iterable[tuple[str, bool]],
    fresh: set[str],
    nodes: int,
) -> tuple[list[list[dict]], dict]:
    """
    Deal parcels into `nodes` disjoint segments of near-equal size.

    owned: (apn, has_vpt) for properties already in the DB, in any key form.
    fresh: normalized APNs that need no lookup this bill year.
    Returns (segments, info). Within a segment, owned parcels come first.
    """
    if nodes < 1:
        raise ValueError("nodes must be >= 1")
    by_norm = dict(parcels)
    owned_norms: list[str] = []
    seen: set[str] = set()
    # VPT payers first: those are the removals that matter most. sorted() is stable.
    for apn, has_vpt in sorted(owned, key=lambda item: not item[1]):
        norm = scanner.normalize_apn(apn)
        if norm in by_norm and norm not in fresh and norm not in seen:
            seen.add(norm)
            owned_norms.append(norm)
    rest = [norm for norm, _ in parcels if norm not in seen and norm not in fresh]

    segments: list[list[dict]] = [[] for _ in range(nodes)]
    owned_counts = [0] * nodes
    for i, norm in enumerate(owned_norms + rest):
        segments[i % nodes].append(by_norm[norm])
        if i < len(owned_norms):
            owned_counts[i % nodes] += 1
    info = {
        "parcels": len(parcels),
        "fresh_skipped": len(parcels) - len(owned_norms) - len(rest),
        "to_scan": len(owned_norms) + len(rest),
        "owned_to_recheck": len(owned_norms),
        "sizes": [len(seg) for seg in segments],
        "owned_per_segment": owned_counts,
    }
    return segments, info


def write_segments(out_dir: Path, fieldnames: list[str], segments: list[list[dict]], city: str) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, rows in enumerate(segments, start=1):
        path = out_dir / f"{city.lower()}_seg{i}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        files.append({
            "segment": i, "file": path.name, "rows": len(rows),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", default="OAKLAND")
    parser.add_argument("--nodes", type=int, default=4)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--no-db", action="store_true", help="skip the DB lookup of properties to re-check")
    args = parser.parse_args(argv)

    city = args.city.strip().upper()
    csv_path = scanner.get_input_csv_path(city=city)
    fieldnames, parcels = read_city_parcels(csv_path, city)
    owned: list[tuple[str, bool]] = []
    if not args.no_db:
        import db
        owned = [(apn, bool(row.get("has_vpt"))) for apn, row in db.get_bills_for_recheck(city).items()]
    segments, info = split_parcels(parcels, owned, fresh_normalized_apns(scanner.load_cache()), args.nodes)

    out_dir = args.out / args.run_id
    files = write_segments(out_dir, fieldnames, segments, city)
    manifest = {
        "run_id": args.run_id, "city": city, "nodes": args.nodes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": csv_path.name, "files": files, **info,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))
    print(f"Wrote {len(files)} segment files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
