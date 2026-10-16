import json
import unittest
from pathlib import Path


REPORT_PATH = Path(__file__).parents[2] / "docs" / "vacancy-research" / "NOO-63.json"


class Noo63VacancyResearchReportTest(unittest.TestCase):
    def test_report_is_valid_and_evidence_bound(self):
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual("004 006300601", report["apn"])
        self.assertIn(report["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(report["confidence"], 0)
        self.assertLessEqual(report["confidence"], 1)
        self.assertLessEqual(len(report["rationale"]), 500)
        self.assertTrue(report["evidence"])
        self.assertTrue(all(item["source_url"].startswith("https://") for item in report["evidence"]))
        self.assertTrue(any("vacant, unpaved" in item["detail"] for item in report["evidence"]))
        self.assertIn("not directly verified", report["duration_note"])


if __name__ == "__main__":
    unittest.main()
