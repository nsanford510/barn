import json
import re
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).parents[2] / "docs" / "vacancy-research" / "NOO-59.json"


class Noo59VacancyResearchReportTest(unittest.TestCase):
    def test_report_is_valid_queue_finding_with_dated_public_evidence(self):
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual("004 003501800", report["apn"])
        self.assertEqual("unknown", report["verdict"])
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertTrue(report["vacancy_start"])
        self.assertTrue(report["vacancy_duration"])
        self.assertTrue(report["report_md"])
        self.assertGreaterEqual(len(report["evidence"]), 1)

        for item in report["evidence"]:
            self.assertRegex(item["source_url"], re.compile(r"^https?://"))
            self.assertRegex(item["observed_at"], re.compile(r"^\d{4}-\d{2}-\d{2}$"))
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])


if __name__ == "__main__":
    unittest.main()
