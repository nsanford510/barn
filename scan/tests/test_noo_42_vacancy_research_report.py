import re
import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).resolve().parents[1] / "research_reports" / "noo42_2617_san_pablo_oakland_vacancy.md"
FINDING_PATH = REPORT_PATH.with_suffix(".json")
BILL_EXCERPT_PATH = REPORT_PATH.with_name("noo42_2617_san_pablo_oakland_county_bill_excerpt.md")


class Noo42VacancyResearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = REPORT_PATH.read_text(encoding="utf-8")
        cls.finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))
        cls.bill_excerpt = BILL_EXCERPT_PATH.read_text(encoding="utf-8")

    def test_report_identifies_target_and_bounded_finding(self) -> None:
        self.assertIn("# Vacancy research: 2617 San Pablo Ave, Oakland", self.report)
        self.assertIn("- **APN:** 003-0005-003-00", self.report)
        self.assertRegex(self.report, r"- \*\*Verdict:\*\* `likely_vacant`")
        confidence = re.search(r"- \*\*Confidence:\*\* ([0-9.]+)", self.report)
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(float(confidence.group(1)), 0)
        self.assertLessEqual(float(confidence.group(1)), 1)

    def test_rationale_and_vacancy_period_are_explicit(self) -> None:
        rationale = re.search(r"- \*\*Rationale:\*\* (.+)", self.report)
        self.assertIsNotNone(rationale)
        self.assertLessEqual(len(rationale.group(1)), 500)
        self.assertIn("fewer than 50 days", rationale.group(1))
        self.assertIn("exact start date and continuous spell are unknown", rationale.group(1))
        self.assertIn("calendar year 2025", self.report)

    def test_evidence_links_and_address_disambiguation_are_retained(self) -> None:
        links = re.findall(r"\]\((https?://[^)\s]+)\)", self.report)
        self.assertGreaterEqual(len(links), 7)
        self.assertTrue(any("gismaps.oaklandca.gov" in link for link in links))
        self.assertTrue(any("propertytax.alamedacountyca.gov" in link for link in links))
        self.assertTrue(any("oaklandca.gov" in link for link in links))
        self.assertIn("054-1785-013-00", self.report)

    def test_queue_candidate_matches_the_report_and_has_rpc_evidence(self) -> None:
        self.assertEqual("003-0005-003-00", self.finding["apn"])
        self.assertEqual("likely_vacant", self.finding["verdict"])
        self.assertGreaterEqual(self.finding["confidence"], 0)
        self.assertLessEqual(self.finding["confidence"], 1)
        self.assertLessEqual(len(self.finding["rationale"]), 500)
        self.assertIsNone(self.finding["vacancy_start"])
        self.assertIn("2025", self.finding["vacancy_duration"])
        self.assertGreaterEqual(len(self.finding["evidence"]), 1)
        for item in self.finding["evidence"]:
            self.assertRegex(item["source_url"], r"^https?://")
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])

    def test_sanitized_county_artifact_retains_charge_and_document_limit(self) -> None:
        self.assertIn("3-5-3", self.bill_excerpt)
        self.assertIn("01387600", self.bill_excerpt)
        self.assertIn("MEAS-W OAKLAND VPT", self.bill_excerpt)
        self.assertIn("$3,000.00", self.bill_excerpt)
        self.assertIn("Internet Copy", self.bill_excerpt)
        self.assertIn("omits owner and mailing information", self.bill_excerpt)


if __name__ == "__main__":
    unittest.main()
