import json
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_reports"
    / "noo-70_004-008500200.json"
)


class Noo70VacancyFindingTest(unittest.TestCase):
    def test_finding_matches_vacancy_rpc_contract(self):
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("004 008500200", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertIn("2022-01-05", finding["vacancy_start"])
        self.assertIn("original vacancy date is unknown", finding["rationale"])


if __name__ == "__main__":
    unittest.main()
