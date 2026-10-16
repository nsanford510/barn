from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "noo-49-869-22nd-st-oakland.md"


class Noo49VacancyReportTests(unittest.TestCase):
    def test_report_has_a_valid_verdict_and_confidence(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertRegex(report, r"(?m)^- Verdict: `(likely_vacant|likely_occupied|unknown)`$")
        match = re.search(r"(?m)^- Confidence: `([0-9.]+)`$", report)
        self.assertIsNotNone(match)
        confidence = float(match.group(1))
        self.assertGreaterEqual(confidence, 0)
        self.assertLessEqual(confidence, 1)

    def test_report_rationale_is_within_the_rpc_limit(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        match = re.search(r"(?m)^- Rationale \(under 500 characters\): (.+)$", report)
        self.assertIsNotNone(match)
        self.assertGreater(len(match.group(1)), 0)
        self.assertLessEqual(len(match.group(1)), 500)

    def test_report_includes_linked_evidence_and_uncertain_duration(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        evidence = report.split("## Evidence", 1)[1].split("## Assessment", 1)[0]
        self.assertGreaterEqual(len(re.findall(r"https?://[^)]+", evidence)), 3)
        self.assertRegex(report, r"(?m)^- Vacancy start: unknown$")
        self.assertRegex(report, r"(?m)^- Vacancy duration: unknown$")


if __name__ == "__main__":
    unittest.main()
