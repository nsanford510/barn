import json
import unittest
from pathlib import Path


REPORT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "vacancy-research"
    / "NOO-60.json"
)


class Noo60VacancyResearchReportTest(unittest.TestCase):
    def test_report_matches_queue_contract_and_has_linked_evidence(self):
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual("004 003902100", report["apn"])
        self.assertEqual("unknown", report["verdict"])
        self.assertIn(
            report["verdict"],
            {"likely_vacant", "likely_occupied", "unknown"},
        )
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertGreaterEqual(len(report["rationale"]), 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertTrue(report["vacancy_start"])
        self.assertTrue(report["vacancy_duration"])
        self.assertTrue(report["report_md"])
        self.assertGreaterEqual(len(report["evidence"]), 1)

        for item in report["evidence"]:
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])
            self.assertTrue(item["source_url"].startswith("https://"))
            self.assertEqual("2026-10-16", item["observed_at"])


if __name__ == "__main__":
    unittest.main()
