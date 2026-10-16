from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "report_003-001503400_20261016.md"


class VacancyResearchReportTests(unittest.TestCase):
    def test_report_has_supported_finding_and_source_links(self) -> None:
        report = REPORT.read_text(encoding="utf-8")

        self.assertIn("APN 003-001503400", report)
        self.assertRegex(report, r"\*\*Verdict:\*\* likely_vacant")
        self.assertRegex(report, r"\*\*Confidence:\*\* 0\.76")
        self.assertIn("at least about 16 months of dated evidence", report)
        self.assertIn("rental listing on 2015-05-05", report)
        self.assertIn("6,758 sq. ft.", report)
        self.assertIn("unimproved land", report)
        self.assertIn("2024-11-14", report)
        self.assertIn("no additions", report)
        links = re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", report)
        self.assertGreaterEqual(len(links), 4)
        self.assertTrue(all(url.startswith("https://") for url in links))


if __name__ == "__main__":
    unittest.main()
