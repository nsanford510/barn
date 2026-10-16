import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).parents[1] / "vacancy_research_reports" / "NOO-53-630-19th-st-oakland.json"


class NOO53VacancyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_finding_matches_queue_contract(self):
        self.assertEqual(self.report["apn"], "003 004100600")
        self.assertIn(self.report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(self.report["confidence"], 0)
        self.assertLessEqual(self.report["confidence"], 1)
        self.assertGreater(len(self.report["rationale"]), 0)
        self.assertLessEqual(len(self.report["rationale"]), 500)

    def test_evidence_has_public_http_sources_and_observation_dates(self):
        self.assertTrue(self.report["evidence"])
        for item in self.report["evidence"]:
            self.assertTrue(item["source_url"].startswith("https://"))
            self.assertTrue(item["observed_at"])

    def test_unknown_duration_is_explicitly_explained(self):
        self.assertIsNone(self.report["vacancy_start"])
        self.assertIsNone(self.report["vacancy_duration"])
        self.assertIn("not established", self.report["vacancy_duration_note"].lower())


if __name__ == "__main__":
    unittest.main()
