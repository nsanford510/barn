from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research" / "NOO-61.json"


class Noo61VacancyResearchReportTests(unittest.TestCase):
    def test_report_has_bounded_queue_finding_and_dated_public_evidence(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("004004900800", report["apn"])
        self.assertIn(report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertGreater(len(report["rationale"]), 0)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertIn("2016-04-21", report["vacancy_start"])
        self.assertIn("continuity", report["vacancy_duration"])

        evidence = report["evidence"]
        self.assertGreaterEqual(len(evidence), 3)
        self.assertTrue(all(item["signal"] and item["detail"] for item in evidence))
        self.assertTrue(all(item["source_url"].startswith("https://") for item in evidence))
        self.assertTrue(all(item["observed_at"] for item in evidence))
        self.assertTrue(any("2016-04-21" == item["observed_at"] for item in evidence))
        self.assertTrue(any("004 004900800" in item["detail"] for item in evidence))

    def test_report_qualifies_the_multi_parcel_and_continuity_limits(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertIn("multi-parcel", report["report_md"])
        self.assertIn("not confirmed continuous vacancy", report["report_md"])
        self.assertIn("vacant land rather than an unoccupied building", report["report_md"])


if __name__ == "__main__":
    unittest.main()
