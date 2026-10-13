from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

from fleet import merge_artifacts as ma


def write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class MergeTests(unittest.TestCase):
    def test_cache_merge_appends_only_newer_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            local = write(tmp / "local.jsonl", [
                {"apn": "A", "has_vpt": True, "checked_at": "2026-10-08T10:00:00+00:00"},
                {"apn": "B", "has_vpt": False},                                           # legacy, no timestamp
                {"apn": "C", "has_vpt": True, "checked_at": "2026-10-09T10:00:00+00:00"},
            ])
            node = write(tmp / "node.jsonl", [
                {"apn": "A", "has_vpt": False, "checked_at": "2026-10-09T01:00:00+00:00"},  # newer: wins
                {"apn": "B", "has_vpt": True, "checked_at": "2026-10-09T01:00:00+00:00"},   # beats legacy
                {"apn": "C", "has_vpt": False, "checked_at": "2026-10-08T01:00:00+00:00"},  # older: ignored
                {"apn": "D", "has_vpt": False, "checked_at": "2026-10-09T01:00:00+00:00"},  # new APN
            ])
            self.assertEqual(ma.merge_cache(local, [node]), 3)
            last = {}
            for row in read(local):
                last[row["apn"]] = row               # same rule load_cache uses: last line wins
            self.assertFalse(last["A"]["has_vpt"])
            self.assertTrue(last["B"]["has_vpt"])
            self.assertTrue(last["C"]["has_vpt"])
            self.assertIn("D", last)

    def test_cache_merge_is_idempotent_and_tolerates_missing_or_corrupt_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            local = write(tmp / "local.jsonl", [])
            node = tmp / "node.jsonl"
            node.write_text('{"apn": "A", "checked_at": "2026-10-09T01:00:00+00:00"}\nnot json\n\n')
            self.assertEqual(ma.merge_cache(local, [node, tmp / "missing.jsonl"]), 1)
            self.assertEqual(ma.merge_cache(local, [node]), 0)

    def test_cache_merge_creates_the_local_file_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            node = write(tmp / "node.jsonl", [{"apn": "A", "checked_at": "2026-10-09T01:00:00+00:00"}])
            local = tmp / "local.jsonl"
            self.assertEqual(ma.merge_cache(local, [node]), 1)
            self.assertEqual(len(read(local)), 1)

    def test_removed_log_merge_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            entry = {"apn": "A", "action": "removed", "at": "2026-10-09T01:00:00+00:00"}
            local = write(tmp / "local.jsonl", [entry])
            node = write(tmp / "node.jsonl", [entry, {"apn": "B", "action": "unflagged", "at": "2026-10-09T02:00:00+00:00"}])
            self.assertEqual(ma.merge_removed_log(local, [node]), 1)
            self.assertEqual(ma.merge_removed_log(local, [node]), 0)
            self.assertEqual([r["apn"] for r in read(local)], ["A", "B"])

    def test_merge_all_reads_node_directories_and_keeps_a_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            scan, download = tmp / "scan", tmp / "dl"
            scan.mkdir()
            write(scan / "measw_cache.jsonl", [{"apn": "A", "checked_at": "2026-10-08T00:00:00+00:00"}])
            write(scan / "vpt_removed.jsonl", [])
            write(download / "node-1" / "measw_cache.jsonl", [{"apn": "B", "checked_at": "2026-10-09T00:00:00+00:00"}])
            write(download / "node-2" / "measw_cache.jsonl", [{"apn": "C", "checked_at": "2026-10-09T00:00:00+00:00"}])
            write(download / "node-2" / "vpt_removed.jsonl", [{"apn": "C", "action": "removed", "at": "x"}])

            result = ma.merge_all(download, scan)

            self.assertEqual(result, {"nodes": 2, "measw_cache.jsonl": 2, "vpt_removed.jsonl": 1})
            self.assertEqual(len(read(scan / "measw_cache.jsonl")), 3)
            self.assertEqual(len(read(scan / "measw_cache.pre-merge.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
