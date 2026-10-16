import json
import unittest
from pathlib import Path


REPORT = Path(__file__).parents[1] / "research_reports" / "NOO-28_578-7th-st-oakland.json"


class Noo28VacancyResearchReportTest(unittest.TestCase):
    def test_report_is_valid_queue_finding_with_linked_evidence(self):
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("001-0209-015-00", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        for evidence in finding["evidence"]:
            self.assertRegex(evidence["source_url"], r"^https?://")
            self.assertTrue(evidence["source_name"])
            self.assertTrue(evidence["details"])

    def test_uncertain_duration_is_not_reported_as_continuous(self):
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("unknown", finding["verdict"])
        self.assertIn("continuous duration", finding["vacancy_duration"])
        self.assertIn("unknown", finding["vacancy_duration"])


if __name__ == "__main__":
    unittest.main()
