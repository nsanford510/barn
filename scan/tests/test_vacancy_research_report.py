"""Keep the NOO-5 research report aligned with the vacancy queue contract."""
import json
import re
import unittest
from pathlib import Path


REPORT = Path(__file__).parents[1] / "research_reports" / "vacancy_001-011100100_229_brush_st_oakland.md"


def load_finding():
    report = REPORT.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*?\})\s*```", report, flags=re.DOTALL)
    if not match:
        raise AssertionError("report must include a JSON finding payload")
    return json.loads(match.group(1))


class VacancyResearchReportTests(unittest.TestCase):
    def test_finding_matches_queue_contract(self):
        finding = load_finding()

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIsInstance(finding["evidence"], list)
        self.assertTrue(all(item.get("source_url", "").startswith("https://") for item in finding["evidence"]))

    def test_unknown_verdict_does_not_invent_vacancy_timing(self):
        finding = load_finding()

        self.assertEqual("unknown", finding["verdict"])
        self.assertIsNone(finding["estimated_vacancy_start_date"])
        self.assertIsNone(finding["estimated_vacancy_duration"])
        self.assertIn("Vacancy start date: unknown", finding["report_md"])
        self.assertIn("Vacancy duration: unknown", finding["report_md"])
        self.assertIn("https://", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
