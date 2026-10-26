import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).parents[2] / "docs" / "vacancy-research" / "NOO-69.json"


class Noo69VacancyResearchReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_required_finding_fields_are_valid(self):
        report = self.report
        self.assertEqual("NOO-69", report["issue"])
        self.assertEqual("004-0081-010-00", report["apn"])
        self.assertIn(report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)

    def test_vacancy_duration_is_not_overstated(self):
        self.assertIsNone(self.report["vacancy_start"])
        self.assertIn("Unknown", self.report["vacancy_duration"])
        self.assertIn("continuous", self.report["vacancy_duration"])

    def test_each_evidence_item_has_a_public_source_and_date(self):
        self.assertGreaterEqual(len(self.report["evidence"]), 1)
        for item in self.report["evidence"]:
            self.assertTrue(item["source_url"].startswith("https://"))
            self.assertTrue(item["observed_at"])
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])

    def test_incomplete_permit_index_is_only_a_limitation(self):
        self.assertTrue(any("permit number, type, or status" in item for item in self.report["limitations"]))


if __name__ == "__main__":
    unittest.main()
