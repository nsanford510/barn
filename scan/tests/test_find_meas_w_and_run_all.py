import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import find_meas_w_addresses as scanner
import run_all


def _ok_result(**fields):
    return scanner._tax_result("ok", bill_url="https://x/view-bill", roll_year=2026, bill_html="<html/>", **fields)


class _FakeElement:
    def __init__(self, attrs=None, inputs=None):
        self._attrs = attrs or {}
        self._inputs = inputs or {}

    def get_attribute(self, name):
        return self._attrs.get(name)

    def query_selector(self, selector):
        name = selector.split('"')[1]
        return _FakeElement({"value": self._inputs[name]}) if name in self._inputs else None

    def evaluate(self, script):
        return None


class _FakePage:
    """Stands in for a Playwright page that lands on a bill for `bill_html`."""

    def __init__(self, bill_html, forms=1, summary_html="<html>summary</html>", token_content_type="application/json"):
        self._token_content_type = token_content_type
        self.url = "https://propertytax.alamedacountyca.gov/search"
        self._bill_html = bill_html
        self._forms = [_FakeElement(inputs={"rollYear": "2026", "taxType": "SEC"}) for _ in range(forms)]
        self._summary_html = summary_html
        self._on_bill = False

    def goto(self, *args, **kwargs):
        pass

    def evaluate(self, *args, **kwargs):
        pass

    def wait_for_url(self, *args, **kwargs):
        self.url = "https://propertytax.alamedacountyca.gov/account-summary"

    def wait_for_selector(self, *args, **kwargs):
        pass

    def query_selector_all(self, selector):
        return self._forms

    def expect_response(self, pattern, **kwargs):
        content_type = self._token_content_type

        class _Info:
            value = _FakeElement()
            value.headers = {"content-type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Info()

    def expect_navigation(self, **kwargs):
        page = self

        class _Nav:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                page._on_bill = True
                page.url = "https://propertytax.alamedacountyca.gov/view-bill"
                return False

        return _Nav()

    def content(self):
        return self._bill_html if self._on_bill else self._summary_html


class TestFindMeasWAndRunAll(unittest.TestCase):
    def test_get_input_csv_path_by_city(self):
        oakland_path = scanner.get_input_csv_path(city="OAKLAND")
        self.assertTrue(oakland_path.exists())
        self.assertEqual(oakland_path.name, "oakland.csv")

        berkeley_path = scanner.get_input_csv_path(city="BERKELEY")
        self.assertTrue(berkeley_path.exists())
        self.assertEqual(berkeley_path.name, "berkeley.csv")

    def test_get_cities_from_csv(self):
        cities = run_all.get_cities_from_csv()
        self.assertIn("OAKLAND", cities)
        self.assertIn("BERKELEY", cities)
        self.assertNotIn("CITY", cities)

    def test_check_bill_vpt_markers(self):
        # Oakland Measure W
        oakland_html = "<tr><td>MEAS-W OAKLAND VPT</td><td>$6,000.00</td></tr>"
        has_vpt, marker = scanner.check_bill_vpt(oakland_html, city="OAKLAND")
        self.assertTrue(has_vpt)
        self.assertEqual(marker, "MEAS-W OAKLAND VPT $6,000.00")

        # The $3,000 condo/duplex rate counts too, and the amount is not taken from another row
        condo_html = (
            "<tr><td>MEAS-W OAKLAND VPT</td><td>b 855-831-1188</td><td>$3,000.00</td></tr>"
            "<tr><td>OTHER</td><td>$12.00</td></tr>"
        )
        has_vpt, marker = scanner.check_bill_vpt(condo_html, city="OAKLAND")
        self.assertTrue(has_vpt)
        self.assertEqual(marker, "MEAS-W OAKLAND VPT $3,000.00")

        # Berkeley Measure M marker
        berkeley_html = "<tr><td>MEAS-M BERKELEY</td><td>$6,000.00</td></tr>"
        has_vpt, marker = scanner.check_bill_vpt(berkeley_html, city="BERKELEY")
        self.assertTrue(has_vpt)
        self.assertEqual(marker, "MEAS-M BERKELEY $6,000.00")

        # Berkeley fixed charges $6,000.00
        berkeley_fc = '<table class="fixed-charges"><tr><td>BERKELEY VACANT TAX</td><td>$6,000.00</td></tr></table>'
        has_vpt, marker = scanner.check_bill_vpt(berkeley_fc, city="BERKELEY")
        self.assertTrue(has_vpt)

        # Non-VPT bill with $756,000.00 valuation should NOT match
        non_vpt_html = '<tr><td>IMPROVEMENTS</td><td> $756,000.00 </td></tr>'
        has_vpt, marker = scanner.check_bill_vpt(non_vpt_html, city="BERKELEY")
        self.assertFalse(has_vpt)
        self.assertIsNone(marker)

    def test_extract_bill_fields_from_html(self):
        html = '''
        Parcel Number:</strong> <span class="no-link">054 174201000</span>
        Location of Property:</strong> 1234 SHATTUCK AVE
        Tax Year: 2024-2025
        PAID DEC 10, 2024
        <table class="fixed-charges">
            <tr><td>MEAS-M BERKELEY</td><td>$3,000.00</td></tr>
        </table>
        '''
        fields = scanner.extract_bill_fields_from_html(html, city="BERKELEY")
        self.assertEqual(fields["parcel_number"], "054 174201000")
        self.assertEqual(fields["location_of_property"], "1234 SHATTUCK AVE")
        self.assertEqual(fields["tax_year"], "2024-2025")
        self.assertEqual(fields["last_payment"], "DEC 10, 2024")
        self.assertEqual(fields["has_vpt"], "1")
        self.assertEqual(fields["vpt_marker"], "MEAS-M BERKELEY $3,000.00")


    def test_zip_from_row_json(self):
        self.assertEqual(scanner._zip_from_row_json('{"ZIPCODE": "94606"}'), "94606")
        self.assertEqual(scanner._zip_from_row_json('{"SitusZip": "94607"}'), "94607")
        self.assertIsNone(scanner._zip_from_row_json('{"CITY": "Oakland"}'))
        self.assertIsNone(scanner._zip_from_row_json(""))
        self.assertIsNone(scanner._zip_from_row_json(None))
        self.assertIsNone(scanner._zip_from_row_json("not json"))

    def test_safe_url(self):
        url_with_spaces = "https://propertytax.alamedacountyca.gov/account-summary?apn=036 244200400"
        safe = scanner._safe_url(url_with_spaces)
        self.assertNotIn(" ", safe)
        self.assertIn("036%20244200400", safe)

    def test_stop_scan(self):
        scanner._stop_requested = False
        run_all.scan_state["is_running"] = True
        run_all.scan_state["continuous_mode"] = True

        stopped = run_all.stop_scan()
        self.assertTrue(stopped)
        self.assertFalse(run_all.scan_state["continuous_mode"])
        self.assertTrue(scanner.is_stop_requested())

        # Cleanup
        scanner._stop_requested = False
        run_all.scan_state["is_running"] = False

    def test_normalize_apn_matches_csv_and_bill_formats(self):
        pairs = [
            ("41-4133-12-2", "041 413301202"),
            ("48H-7576-7-2", "048H757600702"),
            ("46-5424-20", "046 542402000"),
            ("4-99-1", "004 009900100"),
        ]
        for bill_form, csv_form in pairs:
            self.assertEqual(scanner.normalize_apn(bill_form), scanner.normalize_apn(csv_form))
        self.assertNotEqual(scanner.normalize_apn("41-4133-12-2"), scanner.normalize_apn("041 413301203"))
        self.assertEqual(scanner.normalize_apn(None), "")

    def test_page_check_verifies_bill_belongs_to_apn(self):
        bill = (
            'Parcel Number:</strong> <span class="no-link">41-4133-12-2</span>'
            "<tr><td>MEAS-W OAKLAND VPT</td><td>$6,000.00</td></tr>"
        )
        result = scanner.check_property_taxes_with_page("041 413301202", _FakePage(bill), city="OAKLAND")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["has_vpt"])
        self.assertEqual(result["roll_year"], 2026)

        # Same bill returned for a different parcel (stale session): unusable.
        result = scanner.check_property_taxes_with_page("010 083101100", _FakePage(bill), city="OAKLAND")
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["has_vpt"])

    def test_page_check_statuses_without_a_bill(self):
        check = scanner.check_property_taxes_with_page
        self.assertEqual(check("041 413301202", _FakePage("", forms=0))["status"], "no_bill")
        blocked = _FakePage("", forms=0, summary_html="<title>Request Rejected</title>")
        self.assertEqual(check("041 413301202", blocked)["status"], "rejected")
        challenge = _FakePage("", forms=0, summary_html='<script>window["bobcmn"]</script>')
        self.assertEqual(check("041 413301202", challenge)["status"], "error")
        # The WAF answers the CSRF token fetch with an HTML rejection page when rate-limiting.
        rate_limited = _FakePage("", token_content_type="text/html")
        self.assertEqual(check("041 413301202", rate_limited)["status"], "rejected")
        rejected_bill = _FakePage("<title>Request Rejected</title>")
        self.assertEqual(check("041 413301202", rejected_bill)["status"], "rejected")

    def test_cache_freshness(self):
        now = datetime(2026, 10, 8, tzinfo=timezone.utc)
        recent = (now - timedelta(days=3)).isoformat()
        old = (now - timedelta(days=45)).isoformat()
        fresh = lambda entry: scanner.is_cache_fresh(entry, now=now)

        self.assertEqual(scanner.current_roll_year(now), 2026)
        self.assertEqual(scanner.current_roll_year(datetime(2026, 9, 30, tzinfo=timezone.utc)), 2025)

        self.assertFalse(fresh(None))
        # Legacy entries: failed lookups were cached as negatives; last year's bill is stale.
        self.assertFalse(fresh({"bill_url": None, "roll_year": None}))
        self.assertFalse(fresh({"bill_url": "u", "roll_year": 2025}))
        self.assertTrue(fresh({"bill_url": "u", "roll_year": 2026}))
        # New entries
        self.assertTrue(fresh({"status": "ok", "bill_url": "u", "roll_year": 2026, "checked_at": old}))
        self.assertTrue(fresh({"status": "ok", "bill_url": "u", "roll_year": 2021, "checked_at": recent}))
        self.assertFalse(fresh({"status": "ok", "bill_url": "u", "roll_year": 2021, "checked_at": old}))
        self.assertTrue(fresh({"status": "no_bill", "roll_year": None, "checked_at": recent}))
        self.assertFalse(fresh({"status": "error", "roll_year": 2026, "checked_at": recent}))

    def test_cache_round_trip_records_status(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scanner, "CACHE_JSONL", Path(tmp) / "cache.jsonl"):
            scanner.append_cache("001", True, False, "u", 2026, "MEAS-W OAKLAND VPT $6,000.00")
            scanner.append_cache("002", False, False, None, None, status="no_bill")
            cache = scanner.load_cache()
        self.assertEqual(cache["001"]["status"], "ok")
        self.assertTrue(cache["001"]["has_vpt"])
        self.assertTrue(scanner.is_cache_fresh(cache["002"]))

    def _apply(self, result, db_row, curated=False, dry_run=False):
        fake_db = MagicMock()
        with tempfile.TemporaryDirectory() as tmp, patch.dict("sys.modules", {"db": fake_db}), patch.object(
            scanner, "upsert_db"
        ) as upsert, patch.object(scanner, "REMOVED_JSONL", Path(tmp) / "removed.jsonl"):
            action = scanner.apply_scan_result("001", result, '{"APN": "001"}', db_row, curated, dry_run=dry_run)
            log_path = Path(tmp) / "removed.jsonl"
            logged = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
        return action, fake_db, upsert, logged

    def test_apply_adds_property_with_vpt(self):
        action, fake_db, upsert, _ = self._apply(_ok_result(has_vpt=True, vpt_marker="MEAS-W OAKLAND VPT $6,000.00"), None)
        self.assertEqual(action, "added")
        upsert.assert_called_once_with("001", "https://x/view-bill", "<html/>", '{"APN": "001"}')
        fake_db.bulk_delete_bills.assert_not_called()

        action, _, upsert, _ = self._apply(_ok_result(has_vpt=True), {"apn": "001", "has_vpt": 1})
        self.assertEqual(action, "updated")
        upsert.assert_called_once()

    def test_apply_does_not_add_delinquent_only_property(self):
        action, fake_db, upsert, _ = self._apply(_ok_result(is_delinquent=True), None)
        self.assertEqual(action, "unchanged")
        upsert.assert_not_called()
        fake_db.bulk_delete_bills.assert_not_called()

    def test_apply_removes_property_without_vpt(self):
        row = {"apn": "001", "has_vpt": 1, "delinquent": 0, "location_of_property": "1 MAIN ST"}
        action, fake_db, upsert, logged = self._apply(_ok_result(), row)
        self.assertEqual(action, "removed")
        fake_db.bulk_delete_bills.assert_called_once_with(["001"])
        upsert.assert_not_called()
        self.assertEqual(logged[0]["action"], "removed")
        self.assertEqual(logged[0]["location_of_property"], "1 MAIN ST")

        # Delinquent-only rows are removed too (VPT only).
        action, fake_db, _, _ = self._apply(_ok_result(is_delinquent=True), {"apn": "001", "has_vpt": 0, "delinquent": 1})
        self.assertEqual(action, "removed")
        fake_db.bulk_delete_bills.assert_called_once_with(["001"])

    def test_apply_unflags_curated_property_instead_of_deleting(self):
        row = {"apn": "001", "has_vpt": 1, "delinquent": 0}
        action, fake_db, _, logged = self._apply(_ok_result(is_delinquent=True), row, curated=True)
        self.assertEqual(action, "unflagged")
        fake_db.bulk_delete_bills.assert_not_called()
        fake_db.update_bill_fields.assert_called_once_with("001", {"has_vpt": 0, "vpt_marker": None, "delinquent": 1})
        self.assertEqual(logged[0]["action"], "unflagged")

        # Already unflagged: nothing left to do.
        action, fake_db, _, logged = self._apply(_ok_result(), {"apn": "001", "has_vpt": 0, "delinquent": 1}, curated=True)
        self.assertEqual(action, "unchanged")
        fake_db.update_bill_fields.assert_not_called()
        self.assertEqual(logged, [])

    def test_apply_ignores_unverified_lookups(self):
        row = {"apn": "001", "has_vpt": 1}
        for status, expected in (("error", "error"), ("rejected", "error"), ("no_bill", "no_bill")):
            action, fake_db, upsert, logged = self._apply(scanner._tax_result(status), row)
            self.assertEqual(action, expected)
            fake_db.bulk_delete_bills.assert_not_called()
            fake_db.update_bill_fields.assert_not_called()
            upsert.assert_not_called()
            self.assertEqual(logged, [])

    def test_apply_dry_run_changes_nothing(self):
        action, fake_db, upsert, logged = self._apply(_ok_result(), {"apn": "001", "has_vpt": 1}, dry_run=True)
        self.assertEqual(action, "removed")
        fake_db.bulk_delete_bills.assert_not_called()
        self.assertEqual(logged, [])

        action, _, upsert, _ = self._apply(_ok_result(has_vpt=True), None, dry_run=True)
        self.assertEqual(action, "added")
        upsert.assert_not_called()

    def _run_main(self, cache, db_rows, lookups, curated=(), **kwargs):
        """Run scanner.main over a 3-parcel CSV with the tax site and DB faked."""
        fake_db = MagicMock()
        fake_db.get_bills_for_recheck.return_value = dict(db_rows)
        fake_db.get_curated_apns.return_value = set(curated)
        scanned: list[str] = []

        def fake_scan(apns, city, on_result, max_workers=None):
            for apn in apns:
                scanned.append(apn)
                on_result(apn, lookups[apn])
            return True

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "oakland.csv"
            csv_path.write_text("APN,CITY,ADDRESS\n001,Oakland,1 A St\n002,Oakland,2 B St\n003,Oakland,3 C St\n")
            with patch.dict("sys.modules", {"db": fake_db}), patch.object(
                scanner, "get_input_csv_path", return_value=csv_path
            ), patch.object(scanner, "CACHE_JSONL", tmp_path / "cache.jsonl"), patch.object(
                scanner, "REMOVED_JSONL", tmp_path / "removed.jsonl"
            ), patch.object(scanner, "load_cache", return_value=cache), patch.object(
                scanner, "scan_apns", side_effect=fake_scan
            ), patch.object(scanner, "upsert_db") as upsert:
                stats = scanner.main(city="OAKLAND", **kwargs)
        return stats, scanned, fake_db, upsert

    def test_main_rechecks_db_rows_first_and_skips_fresh_cache(self):
        now = datetime.now(timezone.utc).isoformat()
        year = scanner.current_roll_year()
        cache = {
            # 002 was checked against this year's bill already
            "002": {"status": "ok", "has_vpt": False, "bill_url": "u", "roll_year": year, "checked_at": now},
        }
        db_rows = {"003": {"apn": "003", "has_vpt": 1, "delinquent": 0}}
        lookups = {"001": _ok_result(has_vpt=True, vpt_marker="MEAS-W OAKLAND VPT $6,000.00"), "003": _ok_result()}

        stats, scanned, fake_db, upsert = self._run_main(cache, db_rows, lookups)

        self.assertEqual(scanned, ["003", "001"])
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["added"], 1)
        fake_db.bulk_delete_bills.assert_called_once_with(["003"])
        self.assertEqual(upsert.call_args.args[0], "001")

    def test_main_reports_progress_over_all_parcels(self):
        now = datetime.now(timezone.utc).isoformat()
        cache = {"002": {"status": "ok", "has_vpt": False, "bill_url": "u", "roll_year": scanner.current_roll_year(), "checked_at": now}}
        seen: list[dict] = []
        real_apply = scanner.apply_scan_result

        def recording_apply(*args, **kwargs):
            seen.append(dict(scanner.scan_progress))
            return real_apply(*args, **kwargs)

        with patch.object(scanner, "apply_scan_result", side_effect=recording_apply):
            self._run_main(cache, {}, {"001": _ok_result(), "003": _ok_result()})

        # 3 parcels in the CSV, 1 already cached: starts at 1/3 and ends at 3/3.
        self.assertEqual(seen[0]["scanned"], 1)
        self.assertEqual(seen[0]["total"], 3)
        self.assertEqual(scanner.scan_progress["scanned"], 3)
        self.assertEqual(scanner.scan_progress["total"], 3)
        self.assertGreater(scanner.scan_progress["per_min"], 0)

    def test_scan_state_exposes_progress_only_while_running(self):
        scanner.scan_progress.update(scanned=5, total=10, per_min=12.0)
        with patch.object(run_all, "get_cities_from_csv", return_value=[]), patch.dict(
            run_all.intake_autopilot.intake_state, {"is_running": False}
        ):
            with patch.dict(run_all.scan_state, {"is_running": True}):
                progress = run_all.get_scan_state()["progress"]
                self.assertEqual({k: progress[k] for k in ("scanned", "total", "per_min")}, {"scanned": 5, "total": 10, "per_min": 12.0})
            with patch.dict(run_all.scan_state, {"is_running": False}):
                self.assertIsNone(run_all.get_scan_state()["progress"])

    def test_main_restrict_to_csv_rechecks_only_the_segments_db_rows(self):
        # The CSV (this node's segment) holds 001-003. The DB also holds 003 and a parcel
        # that belongs to another node's segment.
        db_rows = {
            "003": {"apn": "003", "has_vpt": 1, "delinquent": 0},
            "1-1-1": {"apn": "1-1-1", "has_vpt": 1, "delinquent": 0},
        }
        lookups = {apn: _ok_result() for apn in ("001", "002", "003", "1-1-1")}

        _, scanned, _, _ = self._run_main({}, db_rows, lookups, restrict_to_csv=True)
        self.assertEqual(scanned, ["003", "001", "002"])    # DB row first, then discovery; no foreign row

        _, scanned, _, _ = self._run_main({}, db_rows, lookups, restrict_to_csv=False)
        self.assertEqual(scanned[:2], ["003", "1-1-1"])      # city-wide runs recheck every DB row

    def test_main_restrict_to_csv_can_come_from_the_environment(self):
        db_rows = {"9-9-9": {"apn": "9-9-9", "has_vpt": 1, "delinquent": 0}}
        with patch.dict("os.environ", {"VPT_RESTRICT_DB_TO_CSV": "1"}):
            _, scanned, _, _ = self._run_main({}, db_rows, {"001": _ok_result(), "002": _ok_result(), "003": _ok_result()})
        self.assertNotIn("9-9-9", scanned)

    def test_scan_progress_carries_counters_pace_and_rejections(self):
        seen: list[dict] = []
        real_apply = scanner.apply_scan_result

        def recording_apply(*args, **kwargs):
            seen.append(dict(scanner.scan_progress))
            return real_apply(*args, **kwargs)

        with patch.object(scanner, "apply_scan_result", side_effect=recording_apply):
            self._run_main({}, {}, {"001": _ok_result(has_vpt=True), "002": _ok_result(), "003": scanner._tax_result("error")})

        final = scanner.scan_progress
        self.assertEqual((final["added"], final["errors"]), (1, 1))
        self.assertEqual(final["current_apn"], "003")
        self.assertEqual(final["interval"], scanner.LOOKUP_INTERVAL_SEC)
        self.assertEqual(seen[0]["added"], 0)               # counters start from zero for each scan

        with patch.object(scanner, "REJECT_BACKOFF_SEC", 60.0), patch.object(scanner, "LOOKUP_INTERVAL_SEC", 10.0):
            pacer = scanner._Pacer()
            before = scanner.time.time()
            pacer.rejected()
        self.assertEqual(scanner.scan_progress["rejections"], 1)
        self.assertGreaterEqual(scanner.scan_progress["paused_until"], before + 59)
        self.assertEqual(scanner.scan_progress["interval"], 12.5)

    def test_main_reports_aborted_when_the_site_keeps_rejecting(self):
        def fake_scan(apns, city, on_result, max_workers=None):
            return False

        fake_db = MagicMock()
        fake_db.get_bills_for_recheck.return_value = {}
        fake_db.get_curated_apns.return_value = set()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "oakland.csv"
            csv_path.write_text("APN,CITY\n001,Oakland\n")
            with patch.dict("sys.modules", {"db": fake_db}), patch.object(
                scanner, "get_input_csv_path", return_value=csv_path
            ), patch.object(scanner, "load_cache", return_value={}), patch.object(scanner, "scan_apns", side_effect=fake_scan):
                stats = scanner.main(city="OAKLAND")
        self.assertEqual(stats.get("aborted"), 1)

    def test_main_recheck_only_skips_discovery(self):
        db_rows = {"003": {"apn": "003", "has_vpt": 1, "delinquent": 0}}
        stats, scanned, _, _ = self._run_main({}, db_rows, {"003": _ok_result(has_vpt=True)}, recheck_only=True)
        self.assertEqual(scanned, ["003"])
        self.assertEqual(stats["updated"], 1)

    def test_main_does_not_rediscover_parcel_stored_under_dashed_apn(self):
        db_rows = {"41-4133-12-2": {"apn": "41-4133-12-2", "has_vpt": 1, "delinquent": 0}}
        fake_db = MagicMock()
        fake_db.get_bills_for_recheck.return_value = db_rows
        fake_db.get_curated_apns.return_value = set()
        scanned: list[str] = []

        def fake_scan(apns, city, on_result, max_workers=None):
            scanned.extend(apns)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "oakland.csv"
            csv_path.write_text("APN,CITY\n041 413301202,Oakland\n")
            with patch.dict("sys.modules", {"db": fake_db}), patch.object(
                scanner, "get_input_csv_path", return_value=csv_path
            ), patch.object(scanner, "load_cache", return_value={}), patch.object(
                scanner, "scan_apns", side_effect=fake_scan
            ):
                scanner.main(city="OAKLAND")
        self.assertEqual(scanned, ["41-4133-12-2"])

    def test_main_removes_from_verified_cache_without_rescanning(self):
        now = datetime.now(timezone.utc).isoformat()
        year = scanner.current_roll_year()
        entry = {"status": "ok", "has_vpt": False, "is_delinquent": True, "bill_url": "u", "roll_year": year, "checked_at": now}
        cache = {apn: dict(entry) for apn in ("001", "002", "003")}
        db_rows = {
            "001": {"apn": "001", "has_vpt": 0, "delinquent": 1},
            "002": {"apn": "002", "has_vpt": 1, "delinquent": 0},
        }
        stats, scanned, fake_db, _ = self._run_main(cache, db_rows, {}, curated={"002"})

        self.assertEqual(scanned, [])
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["unflagged"], 1)
        fake_db.bulk_delete_bills.assert_called_once_with(["001"])
        fake_db.update_bill_fields.assert_called_once()

    def test_main_does_not_cache_failed_lookups(self):
        fake_db = MagicMock()
        fake_db.get_bills_for_recheck.return_value = {"001": {"apn": "001", "has_vpt": 1}}
        fake_db.get_curated_apns.return_value = set()

        def fake_scan(apns, city, on_result, max_workers=None):
            on_result("001", scanner._tax_result("error"))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "oakland.csv").write_text("APN,CITY\n001,Oakland\n")
            with patch.dict("sys.modules", {"db": fake_db}), patch.object(
                scanner, "get_input_csv_path", return_value=tmp_path / "oakland.csv"
            ), patch.object(scanner, "CACHE_JSONL", tmp_path / "cache.jsonl"), patch.object(
                scanner, "scan_apns", side_effect=fake_scan
            ):
                stats = scanner.main(city="OAKLAND")
                self.assertEqual(scanner.load_cache(), {})
        self.assertEqual(stats["error"], 1)
        fake_db.bulk_delete_bills.assert_not_called()

    def test_scan_apns_retries_rejected_parcel_and_delivers_results(self):
        calls: list[str] = []

        def fake_check(apn, page, city=None):
            calls.append(apn)
            if apn == "002" and calls.count("002") == 1:
                return scanner._tax_result("rejected")
            return _ok_result()

        browser = MagicMock()
        browser.new_context.return_value.new_page.return_value.is_closed.return_value = False
        playwright = MagicMock()
        playwright.__enter__.return_value = MagicMock()
        seen: dict[str, str] = {}
        with patch("playwright.sync_api.sync_playwright", return_value=playwright), patch.object(
            scanner, "get_browser", return_value=(browser, False)
        ), patch.object(scanner, "check_property_taxes_with_page", side_effect=fake_check), patch.object(
            scanner, "REJECT_BACKOFF_SEC", 0.01
        ), patch.object(scanner, "LOOKUP_INTERVAL_SEC", 0), patch.object(scanner, "LOOKUP_INTERVAL_MAX_SEC", 0):
            completed = scanner.scan_apns(
                ["001", "002", "003"], "OAKLAND", lambda apn, r: seen.__setitem__(apn, r["status"]), max_workers=1
            )

        self.assertTrue(completed)
        self.assertEqual(seen, {"001": "ok", "002": "ok", "003": "ok"})
        self.assertEqual(calls.count("002"), 2)

    def test_needs_new_browser_after_many_lookups_or_repeated_failures(self):
        with patch.object(scanner, "RECYCLE_EVERY", 150), patch.object(scanner, "RECYCLE_AFTER_ERRORS", 3):
            self.assertFalse(scanner._needs_new_browser(149, 2))
            self.assertTrue(scanner._needs_new_browser(150, 0))
            self.assertTrue(scanner._needs_new_browser(10, 3))
        with patch.object(scanner, "RECYCLE_EVERY", 0), patch.object(scanner, "RECYCLE_AFTER_ERRORS", 0):
            self.assertFalse(scanner._needs_new_browser(10_000, 10_000))   # both can be switched off

    def _run_worker(self, statuses, recycle_every=0, after_errors=0):
        """Run one scan worker over len(statuses) parcels; returns (browsers launched, closed, results)."""
        browsers: list[MagicMock] = []

        def launch(playwright_instance):
            browser = MagicMock()
            browser.new_context.return_value.new_page.return_value.is_closed.return_value = False
            browsers.append(browser)
            return browser, False

        calls = iter(statuses)
        tasks, results = scanner.queue.Queue(), scanner.queue.Queue()
        for i in range(len(statuses)):
            tasks.put(f"00{i}")
        playwright = MagicMock()
        playwright.__enter__.return_value = MagicMock()
        with patch("playwright.sync_api.sync_playwright", return_value=playwright), patch.object(
            scanner, "get_browser", side_effect=launch
        ), patch.object(
            scanner, "check_property_taxes_with_page", side_effect=lambda apn, page, city=None: _ok_result() if next(calls) == "ok" else scanner._tax_result("error")
        ), patch.object(scanner, "LOOKUP_INTERVAL_SEC", 0), patch.object(scanner, "RECYCLE_EVERY", recycle_every), patch.object(
            scanner, "RECYCLE_AFTER_ERRORS", after_errors
        ):
            scanner._scan_worker(tasks, results, "OAKLAND", scanner._Pacer())
        done = []
        while not results.empty():
            item = results.get()
            if item is not scanner._WORKER_DONE:
                done.append(item)
        return browsers, done

    def test_worker_starts_a_fresh_browser_every_n_lookups(self):
        browsers, done = self._run_worker(["ok"] * 5, recycle_every=2)
        self.assertEqual(len(done), 5)
        self.assertEqual(len(browsers), 3)                         # lookups 1-2, 3-4, 5
        for browser in browsers:
            browser.close.assert_called_once()                     # each one is closed, including the last

    def test_worker_starts_a_fresh_browser_after_consecutive_failures(self):
        browsers, done = self._run_worker(["err", "err", "err", "ok", "ok"], after_errors=3)
        self.assertEqual(len(done), 5)                             # failed parcels are still reported, never lost
        self.assertEqual(len(browsers), 2)                         # restarted once, after the third failure in a row

    def test_a_success_resets_the_failure_streak(self):
        browsers, _ = self._run_worker(["err", "err", "ok", "err", "err", "ok"], after_errors=3)
        self.assertEqual(len(browsers), 1)

    def test_worker_keeps_one_browser_when_recycling_is_off(self):
        browsers, _ = self._run_worker(["ok", "err", "err", "err", "err"])
        self.assertEqual(len(browsers), 1)

    def test_progress_counts_only_verified_lookups(self):
        lookups = {"001": _ok_result(), "002": scanner._tax_result("error"), "003": scanner._tax_result("no_bill")}
        self._run_main({}, {}, lookups)
        self.assertEqual(scanner.scan_progress["scanned"], 2)      # the failed lookup is not "done"
        self.assertEqual(scanner.scan_progress["errors"], 1)
        self.assertEqual(scanner.scan_progress["total"], 3)

    def test_pacer_spaces_lookups_and_slows_down_after_rejection(self):
        with patch.object(scanner, "LOOKUP_INTERVAL_SEC", 0.05), patch.object(scanner, "REJECT_BACKOFF_SEC", 0.1):
            pacer = scanner._Pacer()
            start = scanner.time.time()
            for _ in range(3):
                pacer.wait()
            self.assertGreaterEqual(scanner.time.time() - start, 0.09)

            pacer.rejected()
            self.assertGreater(pacer._interval, 0.05)
            paused = scanner.time.time()
            pacer.wait()
            self.assertGreaterEqual(scanner.time.time() - paused, 0.09)
            self.assertFalse(pacer.aborted)

    def test_pacer_aborts_after_repeated_rejections(self):
        with patch.object(scanner, "REJECT_BACKOFF_SEC", 0.0), patch.object(scanner, "REJECT_BACKOFF_MAX_SEC", 0.0):
            pacer = scanner._Pacer()
            for _ in range(scanner.MAX_CONSECUTIVE_REJECTIONS):
                pacer.rejected()
            self.assertTrue(pacer.aborted)

            pacer = scanner._Pacer()
            for _ in range(scanner.MAX_CONSECUTIVE_REJECTIONS - 1):
                pacer.rejected()
            pacer.ok()
            pacer.rejected()
            self.assertFalse(pacer.aborted)

    def test_run_all_accumulates_scan_stats(self):
        before = dict(run_all.scan_state)
        with patch.object(scanner, "main", return_value={"scanned": 10, "added": 2, "removed": 3, "unflagged": 1}) as main:
            run_all._run_city_scan("OAKLAND")
        main.assert_called_once_with(city="OAKLAND", recheck_only=False, dry_run=False)
        self.assertEqual(run_all.scan_state["removed"], before["removed"] + 3)
        self.assertEqual(run_all.scan_state["unflagged"], before["unflagged"] + 1)
        self.assertEqual(run_all.scan_state["total_hits"], before["total_hits"] + 2)
        run_all.scan_state.update(before)

    def test_is_within_days_and_format_added_at(self):
        from datetime import datetime, timezone, timedelta
        from webgui.app import _is_within_days, _format_added_at

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=5)).isoformat()
        old = (now - timedelta(days=35)).isoformat()

        self.assertTrue(_is_within_days(recent, 30))
        self.assertFalse(_is_within_days(old, 30))
        self.assertFalse(_is_within_days(None))
        self.assertFalse(_is_within_days(""))

        formatted = _format_added_at("2026-03-27T18:08:59.491086+00:00")
        self.assertEqual(formatted, "2026-03-27")
        thirty_one_ago = (now - timedelta(days=31)).strftime("%Y-%m-%d")
        self.assertEqual(_format_added_at(None), thirty_one_ago)


if __name__ == "__main__":
    unittest.main()
