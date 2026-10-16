from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backfill_vacant_since as bf

REF = date(2026, 10, 16)


class DurationToStartTests(unittest.TestCase):
    def test_years_and_months(self) -> None:
        self.assertEqual(bf.duration_to_start(3, 11, REF), "2022-11")
        self.assertEqual(bf.duration_to_start(11, 8, REF), "2015-02")

    def test_crosses_year_boundary(self) -> None:
        self.assertEqual(bf.duration_to_start(0, 10, date(2026, 3, 5)), "2025-05")
        self.assertEqual(bf.duration_to_start(1, 0, date(2026, 1, 20)), "2025-01")


class ExtractTests(unittest.TestCase):
    def value(self, text: str) -> str | None:
        ext = bf.extract_vacant_since(text, REF)
        return ext.value if ext else None

    def test_labelled_start_with_markdown(self) -> None:
        self.assertEqual(self.value("- **Likely vacancy start:** January 29, 2015 (reported demolition)"), "2015-01-29")

    def test_labelled_start_calendar_year(self) -> None:
        self.assertEqual(self.value("Vacancy start date: during calendar year 2024 (exact date unknown)."), "2024")

    def test_vacant_since_at_least_iso(self) -> None:
        self.assertEqual(self.value("The parcel has been vacant since at least 2019-08-02."), "2019-08-02")

    def test_vacant_by_requires_duration(self) -> None:
        self.assertEqual(self.value("Vacant by Feb 2019 (at least 7y 8mo through Oct 2026); exact start unverified."), "2019-02")
        self.assertIsNone(self.value("The lot was documented vacant by 1997-04-18."))

    def test_season_is_year_precision(self) -> None:
        self.assertEqual(self.value("That supports vacancy by fall 2021 (about five years as of October 2026)."), "2021")

    def test_duration_uses_reference_date(self) -> None:
        self.assertEqual(self.value("This supports a vacant-lot period of at least 3 years 11 months."), "2022-11")

    def test_unknown_and_vague_left_blank(self) -> None:
        for text in (
            "Vacancy start: unknown. Vacancy duration: unknown.",
            "Vacancy start/duration: not established.",
            "Marketed for lease since October 2025; may have been vacant since being marketed in October 2025.",
            "Owner purchased the property in 2012 and taxes have been delinquent since 2018.",
            "The parcel was classified as vacant land by February 2019 (about 7 years 8 months before October 2026).",
            "If that inference is right, the site has been vacant for at least about 7 years and 6 months.",
            "The observations are 1 year, 8 months, and 24 days apart.",
            "That suggests upper-floor vacancy began circa 1962–1969 (about 57–64 years by October 2026).",
            "A broker flyer labels its SRO units vacant for 50 years.",
            "12/2022 complaint: 'sat unfinished and vacant for at least 21 years'.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.value(text))

    def test_explicit_date_beats_duration(self) -> None:
        text = "Vacancy start: March 2019.\nvacant for at least 2 years 1 month."
        self.assertEqual(self.value(text), "2019-03")

    def test_future_or_ancient_dates_rejected(self) -> None:
        self.assertIsNone(self.value("Vacancy start: 2031."))
        self.assertIsNone(self.value("Vacancy start: 1850."))


class ValidTests(unittest.TestCase):
    def test_formats(self) -> None:
        for good in ("2015", "2015-01", "2015-01-29"):
            self.assertTrue(bf._valid(good, REF))
        for bad in ("15", "2015-13", "2015-02-30", "2026-11", "1899"):
            self.assertFalse(bf._valid(bad, REF))


if __name__ == "__main__":
    unittest.main()
