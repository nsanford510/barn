from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "NOO-23_630_webster_st_oakland.md"


class Noo23VacancyReportTests(unittest.TestCase):
    def test_report_has_valid_finding_fields_and_public_evidence_links(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        rationale = re.search(r"Rationale \((\d+) chars\): (.+)", report)
        confidence = re.search(r"Confidence: `([0-9.]+)`", report)
        links = re.findall(r"\[Official [^\]]+\]\((https?://[^)]+)\)", report)

        self.assertIn("Verdict: `unknown`", report)
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(float(confidence.group(1)), 0.0)
        self.assertLessEqual(float(confidence.group(1)), 1.0)
        self.assertIsNotNone(rationale)
        self.assertEqual(len(rationale.group(2)), int(rationale.group(1)))
        self.assertLessEqual(len(rationale.group(2)), 500)
        self.assertGreaterEqual(len(links), 3)


if __name__ == "__main__":
    unittest.main()
