import json
import unittest
from pathlib import Path


FINDING_PATH = Path(__file__).resolve().parents[1] / "research_reports" / "NOO-25-380-7th-st-oakland.json"


class Noo25VacancyFindingTests(unittest.TestCase):
    def test_finding_obeys_queue_constraints_and_keeps_unsupported_duration_unknown(self) -> None:
        finding = json.loads(FINDING_PATH.read_text(encoding="utf-8"))

        self.assertEqual("001019302600", finding["apn"])
        self.assertIn(finding["verdict"], {"likely_vacant", "likely_occupied", "unknown"})
        self.assertGreaterEqual(finding["confidence"], 0)
        self.assertLessEqual(finding["confidence"], 1)
        self.assertGreater(len(finding["rationale"]), 0)
        self.assertLessEqual(len(finding["rationale"]), 500)
        self.assertEqual("unknown", finding["verdict"])
        self.assertIsNone(finding["vacancy_start"])
        self.assertIsNone(finding["vacancy_duration"])

        self.assertGreaterEqual(len(finding["evidence"]), 1)
        for item in finding["evidence"]:
            self.assertTrue(item["signal"])
            self.assertTrue(item["detail"])
            self.assertTrue(item["source_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
