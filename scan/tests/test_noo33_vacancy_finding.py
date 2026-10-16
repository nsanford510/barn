from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).resolve().parents[1] / "research_reports" / "noo-33-1006-jefferson-st-oakland.json"


class Noo33VacancyFindingTests(unittest.TestCase):
    def test_finding_matches_queue_contract_and_keeps_duration_uncertain(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("likely_occupied", finding["verdict"])
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertGreater(len(finding["evidence"]), 0)
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertIn("duration is undetermined", finding["rationale"])
        self.assertIn("cannot be determined", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
