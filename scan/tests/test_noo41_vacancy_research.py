from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "NOO-41_863_27th_st_oakland.json"


class Noo41VacancyResearchReportTests(unittest.TestCase):
    def test_report_has_a_valid_sourced_verdict_and_does_not_invent_duration(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("NOO-41", report["issue"])
        self.assertEqual("003 000502702", report["apn"])
        self.assertIn(report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertGreater(len(report["rationale"]), 0)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertIsNone(report["vacancy_start"])
        self.assertIsNone(report["vacancy_duration"])
        self.assertTrue(report["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in report["evidence"]))


if __name__ == "__main__":
    unittest.main()
