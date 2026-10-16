import json
import re
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "findings"
    / "NOO-14-271-5th-st-oakland.json"
)


class Noo14VacancyFindingTest(unittest.TestCase):
    def test_finding_matches_research_queue_contract(self):
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("likely_vacant", finding["verdict"])
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertEqual("001-0153-004-00", finding["apn"])
        self.assertIsNone(finding["estimated_vacancy_start_date"])
        self.assertTrue(finding["estimated_vacancy_duration"])
        self.assertTrue(finding["evidence"])
        self.assertTrue(finding["report_md"])

        for item in finding["evidence"]:
            self.assertIn("signal", item)
            self.assertIn("detail", item)
            self.assertTrue(re.match(r"^https?://", item["source_url"]))
            self.assertRegex(item["observed_at"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
