from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "research_reports" / "report_003-001503400_20261015.md"


class VacancyResearchReportTests(unittest.TestCase):
    def test_report_has_supported_unknown_finding_and_source_links(self) -> None:
        report = REPORT.read_text(encoding="utf-8")

        self.assertIn("APN 003-001503400", report)
        self.assertRegex(report, r"\*\*Verdict:\*\* unknown")
        self.assertRegex(report, r"\*\*Confidence:\*\* 0\.35")
        self.assertRegex(report, r"\*\*Vacancy duration:\*\* undetermined")
        self.assertIn("2015 rental listing", report)
        self.assertIn("6,758 sq. ft.", report)
        links = re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", report)
        self.assertGreaterEqual(len(links), 3)
        self.assertTrue(all(url.startswith("https://") for url in links))


if __name__ == "__main__":
    unittest.main()
