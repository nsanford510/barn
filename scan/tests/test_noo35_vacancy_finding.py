from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).parents[1] / "research_reports" / "NOO-35-900-alice-st-suite-500.json"


class Noo35FindingTests(unittest.TestCase):
    def test_finding_matches_vacancy_submission_contract(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("002-0073-055-00", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith(("http://", "https://")) for item in finding["evidence"]))
        self.assertIn("unknown", finding["vacancy_start"])
        self.assertIn("unknown", finding["duration"])


if __name__ == "__main__":
    unittest.main()
