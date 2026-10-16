import json
import unittest
from pathlib import Path


REPORT = Path(__file__).parents[1] / "research_reports" / "noo-55-623-15th-st-oakland.json"


class Noo55VacancyReportTest(unittest.TestCase):
    def test_report_has_supported_finding_and_dated_sources(self):
        report = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("003 007101400", report["apn"])
        self.assertEqual("likely_vacant", report["verdict"])
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertIsNone(report["vacancy_start"])
        self.assertIn("unknown", report["vacancy_duration"])
        self.assertGreaterEqual(len(report["evidence"]), 1)
        for evidence in report["evidence"]:
            self.assertTrue(evidence["source_url"].startswith("https://"))
            self.assertTrue(evidence["observed_at"])


if __name__ == "__main__":
    unittest.main()
