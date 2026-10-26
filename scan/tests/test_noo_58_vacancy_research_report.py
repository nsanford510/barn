from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research" / "NOO-58.json"


class Noo58VacancyResearchReportTests(unittest.TestCase):
    def test_report_is_schema_bounded_and_evidence_linked(self) -> None:
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("NOO-58", finding["issue"])
        self.assertEqual("004-0027-014-00", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsNone(finding["vacancy_start"])
        self.assertIsNone(finding["vacancy_duration"])
        self.assertGreaterEqual(len(finding["evidence"]), 1)
        self.assertTrue(all(item["source"].startswith("https://") for item in finding["evidence"]))
        self.assertTrue(finding["report_md"].strip())


if __name__ == "__main__":
    unittest.main()
