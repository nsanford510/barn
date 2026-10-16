from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).parents[1] / "research_reports" / "NOO-36-920-alice-st-oakland.json"


class Noo36FindingTests(unittest.TestCase):
    def test_finding_matches_vacancy_submission_contract(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("002-0073-060-00", finding["apn"])
        self.assertEqual("unknown", finding["verdict"])
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertIn("unknown", finding["vacancy_start"])
        self.assertIn("unknown", finding["duration"])
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith(("http://", "https://")) for item in finding["evidence"]))
        self.assertTrue(any("1969" in item["detail"] for item in finding["evidence"]))
        self.assertTrue(all("does not establish" in item["detail"] or "nothing about current occupancy" in item["detail"] or "cannot establish" in item["detail"]
                            for item in finding["evidence"][1:]))


if __name__ == "__main__":
    unittest.main()
