import json
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research" / "NOO-64.json"


class Noo64VacancyResearchReportTest(unittest.TestCase):
    def test_report_matches_vacancy_finding_contract(self):
        report = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("004-0063-020-00", report["apn"])
        self.assertEqual("likely_vacant", report["verdict"])
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertIsNone(report["vacancy_start"])
        self.assertEqual("unknown", report["vacancy_duration"])
        self.assertTrue(report["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in report["evidence"]))
        self.assertTrue(any("duration" in item["detail"].lower() for item in report["evidence"]))


if __name__ == "__main__":
    unittest.main()
