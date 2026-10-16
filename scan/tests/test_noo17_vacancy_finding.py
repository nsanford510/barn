import json
import re
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "findings"
    / "noo-17.json"
)


class Noo17VacancyFindingTests(unittest.TestCase):
    def test_finding_is_complete_and_source_linked(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("001 015301400", finding["apn"])
        self.assertEqual("unknown", finding["verdict"])
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIn("duration are unknown", finding["rationale"])
        self.assertGreaterEqual(len(finding["evidence"]), 1)
        for item in finding["evidence"]:
            self.assertRegex(item["source_url"], r"^https?://")
            self.assertTrue(item["detail"])
            self.assertTrue(item["observed_at"])

    def test_report_does_not_claim_a_vacancy_start_or_duration(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))
        report = finding["report_md"]

        self.assertRegex(report, r"\*\*Vacancy start / duration:\*\* unknown")
        self.assertIn("does not establish vacancy", report)
        self.assertEqual(3, len(re.findall(r"https?://[^)]+", report)))


if __name__ == "__main__":
    unittest.main()
