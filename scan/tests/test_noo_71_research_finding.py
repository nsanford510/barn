import json
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).parents[1]
    / "research_agent_kit"
    / "findings"
    / "noo-71-1115-center-st-oakland.json"
)


class Noo71ResearchFindingTest(unittest.TestCase):
    def test_finding_matches_barn_submission_contract(self):
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertTrue(finding["rationale"].strip())
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsInstance(finding["evidence"], list)
        for evidence in finding["evidence"]:
            self.assertTrue(evidence["source_url"].startswith(("https://", "http://")))
            self.assertTrue(evidence["observed_at"].strip())

    def test_finding_does_not_claim_unknown_vacancy_duration(self):
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual(finding["verdict"], "unknown")
        self.assertIn("duration are not established", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
