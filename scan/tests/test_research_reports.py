from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


REPORT_DIR = Path(__file__).resolve().parents[1] / "research_agent_kit" / "reports"
VERDICTS = {"likely_vacant", "likely_occupied", "unknown"}


def load_finding(report_path: Path) -> tuple[str, dict[str, object]]:
    text = report_path.read_text(encoding="utf-8")
    match = re.search(r"```json\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"{report_path.name} has no embedded JSON finding")
    finding = json.loads(match.group(1))
    if not isinstance(finding, dict):
        raise ValueError(f"{report_path.name} finding must be a JSON object")
    return text, finding


class ResearchReportTests(unittest.TestCase):
    def test_embedded_findings_match_queue_rpc_constraints(self) -> None:
        reports = sorted(REPORT_DIR.glob("*.md"))
        self.assertTrue(reports, f"no reports found in {REPORT_DIR}")

        for report_path in reports:
            with self.subTest(report=report_path.name):
                text, finding = load_finding(report_path)
                self.assertIn(f"**APN:**", text)
                self.assertIn(finding.get("verdict"), VERDICTS)

                confidence = finding.get("confidence")
                self.assertIsInstance(confidence, (int, float))
                self.assertGreaterEqual(confidence, 0)
                self.assertLessEqual(confidence, 1)

                rationale = finding.get("rationale")
                self.assertIsInstance(rationale, str)
                self.assertGreater(len(rationale), 0)
                self.assertLessEqual(len(rationale), 500)

                evidence = finding.get("evidence")
                self.assertIsInstance(evidence, list)
                self.assertTrue(evidence)
                for item in evidence:
                    self.assertIsInstance(item, dict)
                    for field in ("signal", "detail", "observed_at", "source_url"):
                        self.assertIsInstance(item.get(field), str)
                        self.assertTrue(item[field])
                    parsed = urlparse(item["source_url"])
                    self.assertIn(parsed.scheme, {"http", "https"})
                    self.assertTrue(parsed.netloc)


if __name__ == "__main__":
    unittest.main()
