import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).parents[2] / "docs" / "vacancy-research" / "NOO-62.json"


class Noo62VacancyResearchReportTest(unittest.TestCase):
    def test_report_has_valid_verdict_and_evidence(self):
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual("NOO-62", report["issue"])
        self.assertEqual("004 004900900", report["apn"])
        self.assertEqual("likely_vacant", report["verdict"])
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertIsNone(report["vacancy_start"])
        self.assertTrue(report["vacancy_duration"].startswith("Unknown."))
        self.assertGreaterEqual(len(report["evidence"]), 1)
        self.assertTrue(all(item["source_url"].startswith("https://") for item in report["evidence"]))


if __name__ == "__main__":
    unittest.main()
