"""Contract checks for the public-evidence research package submitted for NOO-34."""
import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).parents[1] / "research_reports" / "noo-34-vacancy-finding.json"


class Noo34VacancyFindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

    def test_finding_meets_vacancy_rpc_constraints(self):
        finding = self.finding
        self.assertEqual("002 006900301", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsInstance(finding["evidence"], list)
        self.assertTrue(any(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertTrue(all(item.get("signal") and item.get("detail") for item in finding["evidence"]))

    def test_duration_is_presented_as_a_lower_bound(self):
        report = self.finding["report_md"]
        self.assertIn("at least 10 years and 3 months", report)
        self.assertIn("exact date", report)
        self.assertIn("not unused land", report)

    def test_conflicting_address_is_preserved_as_a_limit(self):
        evidence_text = " ".join(item["detail"] for item in self.finding["evidence"])
        self.assertIn("94606", evidence_text)
        self.assertIn("conflicts", evidence_text)


if __name__ == "__main__":
    unittest.main()
