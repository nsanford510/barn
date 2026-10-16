import json
import unittest
from pathlib import Path


REPORT_DIR = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research"


class Noo67VacancyResearchReportTest(unittest.TestCase):
    def test_report_is_sourced_and_does_not_invent_duration(self):
        finding = json.loads((REPORT_DIR / "NOO-67.json").read_text(encoding="utf-8"))
        report = (REPORT_DIR / "NOO-67.md").read_text(encoding="utf-8")

        self.assertEqual("004-007501000", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsNone(finding["vacancy_start"])
        self.assertIsNone(finding["duration"])
        self.assertGreaterEqual(len(finding["evidence"]), 1)
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertIn("2023", report)
        self.assertIn("Unknown", report)
        self.assertIn("2024", report)


if __name__ == "__main__":
    unittest.main()
