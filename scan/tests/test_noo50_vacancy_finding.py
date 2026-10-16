from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).resolve().parents[1] / "research_reports" / "noo-50-814-20th-st-oakland.json"


class Noo50VacancyFindingTests(unittest.TestCase):
    def test_finding_matches_vacancy_submission_contract(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith(("https://", "http://")) for item in finding["evidence"]))
        self.assertIn("vacancy start is unknown", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
