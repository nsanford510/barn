import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "research_reports" / "NOO-30_715-ml-king-jr-way-oakland"


class Noo30VacancyResearchReportTest(unittest.TestCase):
    def test_report_matches_queue_finding_contract(self):
        data = json.loads(REPORT.with_suffix(".json").read_text())
        self.assertEqual(data["issue"], "NOO-30")
        self.assertEqual(data["apn"], "001 021701901")
        self.assertIn(data["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(data["confidence"], 0)
        self.assertLessEqual(data["confidence"], 1)
        self.assertLessEqual(len(data["rationale"]), 500)
        self.assertIsNone(data["vacancy_start"])
        self.assertIsNone(data["vacancy_duration"])
        self.assertTrue(data["sources"])
        self.assertTrue(all(source.startswith("https://") for source in data["sources"]))
        self.assertTrue(data["finding_id"])

    def test_markdown_report_records_same_finding_and_limits(self):
        markdown = REPORT.with_suffix(".md").read_text()
        data = json.loads(REPORT.with_suffix(".json").read_text())
        self.assertIn(data["finding_id"], markdown)
        self.assertIn(data["rationale"], markdown)
        self.assertIn("duration is unknown", markdown)
        for source in data["sources"]:
            self.assertIn(source, markdown)
