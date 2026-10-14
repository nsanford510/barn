from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

import db
from tests.test_db_zip_filter import _FakeClient


class VacancyFilterTests(unittest.TestCase):
    def _run(self, **kwargs):
        client = _FakeClient()
        with patch("db.get_client", return_value=client):
            db.get_bills_with_parcels_filtered(page=1, page_size=25, **kwargs)
        return client

    def test_no_vacancy_filters_adds_no_clauses(self) -> None:
        client = self._run()
        self.assertNotIn("gte", client.log)
        self.assertNotIn("is_", client.log)
        self.assertNotIn("eq", client.log)

    def test_ai_verdict_filter_matches_long_and_short_spellings(self) -> None:
        client = self._run(ai_verdict_filter="likely_vacant")
        self.assertIn((("ai_vacancy_verdict", ["likely_vacant", "vacant"]), {}), client.log["in_"])
        client = self._run(ai_verdict_filter="unknown")
        self.assertIn((("ai_vacancy_verdict", ["unknown"]), {}), client.log["in_"])

    def test_ai_verdict_none_matches_unassessed_rows(self) -> None:
        client = self._run(ai_verdict_filter="none")
        self.assertIn((("ai_vacancy_verdict", None), {}), client.log["is_"])

    def test_ai_verdict_filter_ignores_unknown_values(self) -> None:
        client = self._run(ai_verdict_filter="bogus")
        self.assertNotIn("in_", client.log)

    def test_ai_confidence_accepts_percent_and_fraction(self) -> None:
        for raw in ("70", "0.7"):
            client = self._run(ai_confidence_min=raw)
            (column, value), _ = client.log["gte"][0]
            self.assertEqual("ai_vacancy_confidence", column)
            self.assertAlmostEqual(0.7, value)

    def test_ai_confidence_ignores_invalid_values(self) -> None:
        for raw in ("", "abc", "0", "-5", "500"):
            client = self._run(ai_confidence_min=raw)
            self.assertNotIn("gte", client.log, raw)

    def test_site_vacant_filter(self) -> None:
        for raw, expected in (("yes", True), ("no", False), ("unknown", None)):
            client = self._run(site_vacant_filter=raw)
            self.assertIn((("site_vacant", expected), {}), client.log["is_"])


if __name__ == "__main__":
    unittest.main()
