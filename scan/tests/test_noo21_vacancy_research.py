from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "reports"
    / "NOO-21.json"
)


class Noo21FindingTests(unittest.TestCase):
    def test_finding_matches_queue_contract_and_has_cited_evidence(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        for item in finding["evidence"]:
            self.assertTrue(item["source_url"].startswith(("http://", "https://")))
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])
            self.assertTrue(item["observed_at"])
        self.assertIn("Vacancy start/duration", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
