import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research" / "NOO-56.json"


class Noo56VacancyResearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_finding_matches_queue_contract(self):
        self.assertEqual("003-0071-021-00", self.report["apn"])
        self.assertIn(self.report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(self.report["confidence"], 0)
        self.assertLessEqual(self.report["confidence"], 1)
        self.assertLessEqual(len(self.report["rationale"]), 500)
        self.assertTrue(self.report["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in self.report["evidence"]))

    def test_vacancy_duration_does_not_claim_continuity_or_move_out_date(self):
        self.assertEqual("upper_bound_only", self.report["vacancy_start"]["status"])
        self.assertEqual("unknown", self.report["duration"]["status"])
        self.assertIn("continuous vacancy", self.report["duration"]["detail"].lower())
        self.assertTrue(any("current" in limitation.lower() for limitation in self.report["limitations"]))


if __name__ == "__main__":
    unittest.main()
