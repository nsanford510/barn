"""Structural checks for the public evidence report submitted for NOO-16."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


SCAN_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = SCAN_DIR / "research_reports"


class VacancyResearchReportTests(unittest.TestCase):
    def test_noo_16_report_has_a_valid_rpc_evidence_payload(self) -> None:
        reports = sorted(REPORTS_DIR.glob("report_001-0153-008-00_*.md"))
        self.assertTrue(reports, "NOO-16 report is missing")
        report = reports[-1].read_text(encoding="utf-8")

        verdict = re.search(r"\*\*Verdict\*\*: `([^`]+)`", report)
        confidence = re.search(r"\*\*Confidence\*\*: ([0-9.]+)", report)
        rationale = re.search(r"\*\*Rationale\*\*: (.+)", report)
        self.assertIsNotNone(verdict)
        self.assertIn(verdict.group(1), {"likely_vacant", "likely_occupied", "unknown"})
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(float(confidence.group(1)), 0)
        self.assertLessEqual(float(confidence.group(1)), 1)
        self.assertIsNotNone(rationale)
        self.assertLessEqual(len(rationale.group(1)), 500)

        payload_match = re.search(r"```json\n(.*?)\n```", report, re.DOTALL)
        self.assertIsNotNone(payload_match)
        evidence = json.loads(payload_match.group(1))
        self.assertIsInstance(evidence, list)
        for item in evidence:
            self.assertIsInstance(item, dict)
            self.assertTrue(item.get("signal"))
            self.assertTrue(item.get("detail"))
            parsed = urlparse(item.get("source_url", ""))
            self.assertIn(parsed.scheme, {"http", "https"})
            self.assertTrue(parsed.netloc)


if __name__ == "__main__":
    unittest.main()
