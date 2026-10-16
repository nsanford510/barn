import json
from pathlib import Path
import unittest


REPORT = (
    Path(__file__).parents[1]
    / "research_agent_kit"
    / "reports"
    / "noo-7-726-4th-st-oakland.json"
)


class Noo7VacancyResearchReportTest(unittest.TestCase):
    def test_report_matches_vacancy_submission_contract(self):
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertGreaterEqual(len(finding["evidence"]), 1)
        self.assertTrue(finding["report_md"])

        for evidence in finding["evidence"]:
            self.assertTrue(evidence["signal"])
            self.assertTrue(evidence["detail"])
            self.assertRegex(evidence["source_url"], r"^https?://")
            self.assertTrue(evidence["observed_at"])

    def test_report_does_not_claim_an_unverified_start_or_duration(self):
        finding = json.loads(REPORT.read_text(encoding="utf-8"))

        self.assertEqual("unknown", finding["verdict"])
        self.assertIn("start date and duration are unknown", finding["rationale"])


if __name__ == "__main__":
    unittest.main()
