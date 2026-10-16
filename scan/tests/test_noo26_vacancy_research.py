import json
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).parents[1]
    / "research_agent_kit"
    / "findings"
    / "noo-26-001-0195-006-00.json"
)


class Noo26VacancyResearchTest(unittest.TestCase):
    def test_finding_matches_vacancy_queue_contract(self):
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("001-0195-006-00", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertEqual("likely_vacant", finding["verdict"])
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertGreaterEqual(len(finding["evidence"]), 4)
        for item in finding["evidence"]:
            self.assertTrue(item["source_url"].startswith(("https://", "http://")))
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])
            self.assertTrue(item["observed_at"])
        self.assertIn("1962–1969", finding["report_md"])
        self.assertIn("continuity between those observations is unverified", finding["report_md"])
        self.assertIn("no visible publication date", finding["evidence"][0]["detail"])
        self.assertIn("does not establish current occupancy", finding["evidence"][1]["detail"])


if __name__ == "__main__":
    unittest.main()
