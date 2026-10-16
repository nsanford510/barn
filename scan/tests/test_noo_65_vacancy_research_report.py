from __future__ import annotations

import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "vacancy-research" / "NOO-65.json"


class Noo65VacancyResearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    def test_report_identifies_the_requested_property_and_allowed_verdict(self) -> None:
        self.assertEqual("NOO-65", self.report["issue"])
        self.assertEqual("004-0067-016-00", self.report["apn"])
        self.assertEqual("800 CENTER ST, OAKLAND, CA 94607", self.report["address"])
        self.assertIn(self.report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(self.report["confidence"], 0)
        self.assertLessEqual(self.report["confidence"], 1)

    def test_rationale_is_bounded_and_vacancy_timing_is_not_overstated(self) -> None:
        self.assertGreater(len(self.report["rationale"]), 0)
        self.assertLessEqual(len(self.report["rationale"]), 500)
        self.assertEqual("unknown", self.report["vacancy_start"])
        self.assertIn("do not prove continuous vacancy", self.report["duration"])
        self.assertIn("24 years", self.report["duration"])

    def test_each_evidence_item_has_a_public_source_and_observation(self) -> None:
        self.assertGreaterEqual(len(self.report["evidence"]), 4)
        for item in self.report["evidence"]:
            with self.subTest(source=item["source_title"]):
                self.assertTrue(item["source_url"].startswith("https://"))
                self.assertTrue(item["observed_at"])
                self.assertTrue(item["detail"])

    def test_markdown_report_preserves_the_same_timing_caveat(self) -> None:
        self.assertIn("Vacancy start: unknown", self.report["report_md"])
        self.assertIn("do not establish uninterrupted vacancy", self.report["report_md"])
        self.assertIn("https://deh.acgov.org/landwater-assets/docs/public-notices/RO0000454_FACT_SHT_2021-09-15.pdf", self.report["report_md"])


if __name__ == "__main__":
    unittest.main()
