from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


FINDING = (
    Path(__file__).resolve().parents[1]
    / "research_agent_kit"
    / "findings"
    / "noo-39-2701-san-pablo-ave-oakland.json"
)


class Noo39VacancyFindingTests(unittest.TestCase):
    def test_finding_is_rpc_compatible_and_duration_is_evidence_bounded(self) -> None:
        finding = json.loads(FINDING.read_text(encoding="utf-8"))

        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertGreaterEqual(len(finding["evidence"]), 1)
        for item in finding["evidence"]:
            self.assertTrue(item["signal"].strip())
            self.assertTrue(item["detail"].strip())
            parsed = urlparse(item["source_url"])
            self.assertIn(parsed.scheme, {"http", "https"})
            self.assertTrue(parsed.netloc)
            self.assertTrue(item["observed_at"])
        self.assertIn("Vacancy start:** not confirmed", finding["report_md"])
        self.assertIn("Duration:** unknown", finding["report_md"])


if __name__ == "__main__":
    unittest.main()
