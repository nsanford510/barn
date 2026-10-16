from __future__ import annotations

import json
import unittest
from pathlib import Path


FINDING = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "findings"
    / "noo-20-620-madison-st-oakland.json"
)


class Noo20VacancyFindingTests(unittest.TestCase):
    def test_finding_is_rpc_compatible_and_keeps_unknown_duration_evidence_bound(self) -> None:
        finding = json.loads(FINDING.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertTrue(finding["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in finding["evidence"]))
        self.assertIn("Vacancy start:** unknown", finding["report_md"])
        self.assertIn("Duration:** unknown", finding["report_md"])
        self.assertIn("City of Oakland VPT information", finding["report_md"])
        self.assertTrue(all(item["detail"].strip() for item in finding["evidence"]))


if __name__ == "__main__":
    unittest.main()
