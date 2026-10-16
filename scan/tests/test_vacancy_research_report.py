from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "001013901400.json"


class VacancyResearchReportTests(unittest.TestCase):
    def test_report_meets_queue_finding_contract(self) -> None:
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsNone(finding["estimated_vacancy_start_date"])
        self.assertIsNone(finding["estimated_vacancy_duration"])
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertTrue(all(item["observed_at"] for item in finding["evidence"]))
        self.assertIn("not estimable", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
