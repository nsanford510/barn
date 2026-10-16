import json
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).parents[1]
    / "research_reports"
    / "NOO-66-1396-5th-st-oakland.json"
)


class Noo66VacancyFindingTest(unittest.TestCase):
    def test_finding_is_submit_ready_except_for_claim_token(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("004 006900400", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertEqual("by 2016-05", finding["vacancy_start"])
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertTrue(finding["report_md"])
        self.assertNotIn("claim_token", finding)


if __name__ == "__main__":
    unittest.main()
