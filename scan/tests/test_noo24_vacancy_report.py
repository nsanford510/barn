from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "NOO-24_383_9th_st_oakland.md"


class Noo24VacancyReportTests(unittest.TestCase):
    def test_report_has_valid_finding_fields_and_public_evidence_links(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        rationale = re.search(r"Rationale \((\d+) chars\): (.+)", report)
        confidence = re.search(r"Confidence: `([0-9.]+)`", report)
        links = re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", report)

        self.assertIn("APN 001 019300300", report)
        self.assertIn("Verdict: `unknown`", report)
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(float(confidence.group(1)), 0.0)
        self.assertLessEqual(float(confidence.group(1)), 1.0)
        self.assertIsNotNone(rationale)
        self.assertEqual(len(rationale.group(2)), int(rationale.group(1)))
        self.assertLessEqual(len(rationale.group(2)), 500)
        self.assertIn("Vacancy start: Unknown", report)
        self.assertIn("Duration: Not established", report)
        self.assertGreaterEqual(len(links), 5)
        self.assertIn("not evidence of a vacant-property tax", report)


if __name__ == "__main__":
    unittest.main()
