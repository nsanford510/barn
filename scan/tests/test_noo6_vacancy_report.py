import json
import re
import unittest
from pathlib import Path


REPORT = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "reports"
    / "NOO-6-715-5TH-ST-OAKLAND.md"
)


class Noo6VacancyReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.markdown = REPORT.read_text(encoding="utf-8")
        match = re.search(r"```json\s*(.*?)\s*```", self.markdown, re.DOTALL)
        self.assertIsNotNone(match, "report must contain a JSON finding payload")
        self.finding = json.loads(match.group(1))

    def test_finding_obeys_vacancy_queue_constraints(self) -> None:
        finding = self.finding
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))

    def test_report_does_not_misassign_2012_shutdown_to_target_parcel(self) -> None:
        finding = self.finding
        self.assertEqual("unknown", finding["verdict"])
        self.assertIsNone(finding["vacancy_estimate"]["start_date"])
        self.assertIsNone(finding["vacancy_estimate"]["duration"])
        self.assertIn("715 4th St", finding["rationale"])
        self.assertIn("715 5th St", finding["rationale"])


if __name__ == "__main__":
    unittest.main()
