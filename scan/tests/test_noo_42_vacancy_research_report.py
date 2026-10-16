import re
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).resolve().parents[1] / "research_reports" / "noo42_2617_san_pablo_oakland_vacancy.md"


class Noo42VacancyResearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = REPORT_PATH.read_text(encoding="utf-8")

    def test_report_identifies_target_and_bounded_finding(self) -> None:
        self.assertIn("# Vacancy research: 2617 San Pablo Ave, Oakland", self.report)
        self.assertIn("- **APN:** 003-0005-003-00", self.report)
        self.assertRegex(self.report, r"- \*\*Verdict:\*\* `unknown`")
        confidence = re.search(r"- \*\*Confidence:\*\* ([0-9.]+)", self.report)
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(float(confidence.group(1)), 0)
        self.assertLessEqual(float(confidence.group(1)), 1)

    def test_rationale_and_vacancy_period_are_explicit(self) -> None:
        rationale = re.search(r"- \*\*Rationale:\*\* (.+)", self.report)
        self.assertIsNotNone(rationale)
        self.assertLessEqual(len(rationale.group(1)), 500)
        self.assertRegex(self.report, r"- \*\*Vacancy period:\*\* Unknown\.")
        self.assertIn("no vacancy start or duration", rationale.group(1))

    def test_evidence_links_and_address_disambiguation_are_retained(self) -> None:
        links = re.findall(r"\]\((https?://[^)\s]+)\)", self.report)
        self.assertGreaterEqual(len(links), 5)
        self.assertTrue(any("gismaps.oaklandca.gov" in link for link in links))
        self.assertTrue(any("stopwaste.org" in link for link in links))
        self.assertIn("054-1785-013-00", self.report)
        self.assertIn("unverified, not a confirmed vacancy signal", self.report)


if __name__ == "__main__":
    unittest.main()
