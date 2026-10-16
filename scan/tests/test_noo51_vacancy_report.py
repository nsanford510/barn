"""Guard the issue-specific NOO-51 vacancy research submission fields."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


REPORT = Path(__file__).parents[1] / "research_reports" / "NOO-51-2020-brush-st-oakland.md"


class Noo51VacancyReportTest(unittest.TestCase):
    def test_report_has_queue_compatible_finding_and_cited_sources(self) -> None:
        report = REPORT.read_text(encoding="utf-8")

        self.assertIn("**APN:** 003-0037-005-03", report)
        self.assertIn("**Verdict:** `unknown`", report)
        rationale = re.search(r"\*\*Rationale \(\d+ characters\):\*\* (.+)", report)
        self.assertIsNotNone(rationale)
        self.assertLessEqual(len(rationale.group(1)), 500)
        self.assertIn("**Vacancy start / duration:** Not established.", report)

        source_urls = re.findall(r"https?://[^)\s]+", report)
        self.assertGreaterEqual(len(source_urls), 5)
        self.assertTrue(all(url.startswith(("https://", "http://")) for url in source_urls))


if __name__ == "__main__":
    unittest.main()
