from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

from fleet import split_parcels as sp


def _parcels(n: int) -> list[tuple[str, dict]]:
    out = []
    for i in range(1, n + 1):
        apn = f"{i % 900:03d} {i:09d}"
        out.append((sp.scanner.normalize_apn(apn), {"APN": apn, "CITY": "Oakland"}))
    return out


def _apns(segments: list[list[dict]]) -> list[str]:
    return [sp.scanner.normalize_apn(row["APN"]) for seg in segments for row in seg]


class SplitParcelsTests(unittest.TestCase):
    def test_segments_are_disjoint_complete_and_balanced(self) -> None:
        parcels = _parcels(103)
        segments, info = sp.split_parcels(parcels, [], set(), 4)

        flat = _apns(segments)
        self.assertEqual(len(flat), 103)
        self.assertEqual(len(set(flat)), 103)
        self.assertEqual(set(flat), {norm for norm, _ in parcels})
        self.assertLessEqual(max(info["sizes"]) - min(info["sizes"]), 1)
        self.assertEqual(info["to_scan"], 103)

    def test_fresh_parcels_are_excluded(self) -> None:
        parcels = _parcels(20)
        fresh = {parcels[0][0], parcels[7][0]}
        segments, info = sp.split_parcels(parcels, [], fresh, 4)

        self.assertTrue(fresh.isdisjoint(_apns(segments)))
        self.assertEqual(info["fresh_skipped"], 2)
        self.assertEqual(info["to_scan"], 18)

    def test_owned_parcels_come_first_and_are_spread_evenly(self) -> None:
        parcels = _parcels(40)
        owned = [(parcels[i][1]["APN"], i % 2 == 0) for i in range(10, 22)]  # 12 DB rows
        segments, info = sp.split_parcels(parcels, owned, set(), 4)

        self.assertEqual(info["owned_to_recheck"], 12)
        self.assertEqual(info["owned_per_segment"], [3, 3, 3, 3])
        owned_norms = {sp.scanner.normalize_apn(apn) for apn, _ in owned}
        for seg in segments:
            head = [sp.scanner.normalize_apn(row["APN"]) for row in seg[:3]]
            self.assertTrue(set(head) <= owned_norms)

    def test_vpt_owned_rows_are_dealt_before_delinquent_only(self) -> None:
        parcels = _parcels(12)
        owned = [(parcels[0][1]["APN"], False), (parcels[1][1]["APN"], True), (parcels[2][1]["APN"], False)]
        segments, _ = sp.split_parcels(parcels, owned, set(), 3)

        # The one VPT payer is dealt first, so it heads segment 1.
        self.assertEqual(sp.scanner.normalize_apn(segments[0][0]["APN"]), parcels[1][0])

    def test_dashed_db_apn_matches_spaced_csv_apn(self) -> None:
        parcels = [(sp.scanner.normalize_apn("041 413301202"), {"APN": "041 413301202", "CITY": "Oakland"}),
                   (sp.scanner.normalize_apn("004 009900100"), {"APN": "004 009900100", "CITY": "Oakland"})]
        segments, info = sp.split_parcels(parcels, [("4-99-1", True)], set(), 2)

        self.assertEqual(info["owned_to_recheck"], 1)
        self.assertEqual(segments[0][0]["APN"], "004 009900100")
        # A fresh dashed cache entry also marks the spaced CSV form fresh.
        _, info = sp.split_parcels(parcels, [("4-99-1", True)], {sp.scanner.normalize_apn("4-99-1")}, 2)
        self.assertEqual(info["to_scan"], 1)

    def test_owned_parcels_missing_from_csv_are_ignored(self) -> None:
        segments, info = sp.split_parcels(_parcels(5), [("999-9999-99", True)], set(), 2)
        self.assertEqual(info["owned_to_recheck"], 0)
        self.assertEqual(len(_apns(segments)), 5)

    def test_fewer_parcels_than_nodes(self) -> None:
        segments, info = sp.split_parcels(_parcels(2), [], set(), 4)
        self.assertEqual(info["sizes"], [1, 1, 0, 0])

    def test_invalid_node_count(self) -> None:
        with self.assertRaises(ValueError):
            sp.split_parcels(_parcels(3), [], set(), 0)

    def test_read_city_parcels_filters_city_and_collapses_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oakland.csv"
            path.write_text(
                "APN,CITY,ADDRESS\n"
                "001 000000100,Oakland,1 A St\n"
                "001 000000100,Oakland,1 A St Unit 2\n"
                "002 000000200,Berkeley,2 B St\n"
                "003 000000300,Oakland,3 C St\n",
                encoding="utf-8-sig",
            )
            fieldnames, parcels = sp.read_city_parcels(path, "oakland")

        self.assertEqual(fieldnames, ["APN", "CITY", "ADDRESS"])
        self.assertEqual([row["APN"] for _, row in parcels], ["001 000000100", "003 000000300"])
        self.assertEqual(parcels[0][1]["ADDRESS"], "1 A St")

    def test_write_segments_round_trips_rows(self) -> None:
        parcels = _parcels(7)
        segments, _ = sp.split_parcels(parcels, [], set(), 3)
        with tempfile.TemporaryDirectory() as tmp:
            files = sp.write_segments(Path(tmp), ["APN", "CITY"], segments, "OAKLAND")
            self.assertEqual([f["rows"] for f in files], [3, 2, 2])
            self.assertEqual(files[0]["file"], "oakland_seg1.csv")
            with (Path(tmp) / "oakland_seg1.csv").open(newline="") as f:
                self.assertEqual([r["APN"] for r in csv.DictReader(f)], [r["APN"] for r in segments[0]])


if __name__ == "__main__":
    unittest.main()
