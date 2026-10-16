from __future__ import annotations

import csv
import importlib.util
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))


def _load_webgui_app() -> ModuleType:
    module_name = "test_webgui_app_route_api"
    module_path = SCAN_DIR / "webgui" / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouteApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.webgui_app = _load_webgui_app()

    def _fleet_store(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "scanner_workers": [{"id": "w1", "name": "barn-scan-node-1", "last_heartbeat": now.isoformat(),
                                 "capabilities": {"public_ip": "1.2.3.4"}}],
            "scanner_jobs": [{"id": "j1", "worker_id": "w1", "job_type": "fleet_scan", "status": "running",
                              "scope": {"run_id": "r1", "segment": 1, "of": 4, "total": 200},
                              "processed_count": 50, "started_at": now.isoformat()}],
            "scanner_job_checkpoints": [],
        }

    def test_parse_added_bound_normalises_to_utc_and_ignores_garbage(self) -> None:
        parse = self.webgui_app._parse_added_bound
        self.assertEqual("2026-10-08T23:46:00+00:00", parse("2026-10-08T23:46:00Z"))
        self.assertEqual("2026-10-08T23:46:00+00:00", parse("2026-10-08T16:46:00-07:00"))
        self.assertEqual("2026-10-08T23:46:00+00:00", parse("2026-10-08T23:46:00"))     # no offset: UTC
        self.assertEqual("2026-10-08T23:46:00.123000+00:00", parse("2026-10-08T23:46:00.123Z"))
        self.assertEqual("2026-10-08T00:00:00+00:00", parse("2026-10-08"))               # bare date: midnight UTC
        for bad in ("", "   ", None, "yesterday", "2026-13-45", "x'; drop table bills;--"):
            self.assertIsNone(parse(bad))

    def test_added_range_args_reads_the_query_string(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/?added_after=2026-10-08T23:46:00.000Z&added_before=nonsense"
        ):
            after, before = self.webgui_app._added_range_args()
        self.assertEqual(("2026-10-08T23:46:00+00:00", ""), (after, before))

    def _fake_db(self):
        from unittest.mock import MagicMock
        fake = MagicMock()
        fake.get_bills_with_parcels_filtered.return_value = ([], 0)
        fake.get_favorites_apns.return_value = []
        fake.get_distinct_zips.return_value = []
        fake.get_distinct_cities.return_value = []
        fake.get_lists.return_value = []
        fake.get_list.return_value = None
        return fake

    def test_list_page_filters_the_query_and_hands_the_range_to_the_template(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context(
            "/?added_after=2026-10-08T23:46:00Z&added_before=2026-10-10T00:00:00Z&sort=added_at&order=desc"
        ), patch.object(self.webgui_app, "db", fake), patch.object(
            self.webgui_app, "render_template", return_value="rendered"
        ) as render:
            self.webgui_app.search_page.__wrapped__()

        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual("2026-10-08T23:46:00+00:00", kwargs["added_after"])
        self.assertEqual("2026-10-10T00:00:00+00:00", kwargs["added_before"])
        self.assertEqual(("added_at", "desc"), (kwargs["sort"], kwargs["order"]))
        self.assertEqual("2026-10-08T23:46:00+00:00", render.call_args.kwargs["added_after_filter"])
        self.assertEqual("2026-10-10T00:00:00+00:00", render.call_args.kwargs["added_before_filter"])

    def test_list_page_without_dates_passes_empty_bounds(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context("/"), patch.object(self.webgui_app, "db", fake), patch.object(
            self.webgui_app, "render_template", return_value="rendered"
        ):
            self.webgui_app.search_page.__wrapped__()
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual(("", ""), (kwargs["added_after"], kwargs["added_before"]))

    def test_gallery_page_filters_by_date_added(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context("/gallery?added_before=2026-10-08T23:46:00Z"), patch.object(
            self.webgui_app, "db", fake
        ), patch.object(self.webgui_app, "render_template", return_value="rendered") as render:
            self.webgui_app.gallery_page.__wrapped__()
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual(("", "2026-10-08T23:46:00+00:00"), (kwargs["added_after"], kwargs["added_before"]))
        self.assertEqual("2026-10-08T23:46:00+00:00", render.call_args.kwargs["added_before_filter"])

    def test_select_all_apn_list_uses_the_same_date_range(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context("/api/apn_list?added_after=2026-10-08T23:46:00Z"), patch.object(
            self.webgui_app, "db", fake
        ):
            self.webgui_app.api_apn_list.__wrapped__()
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual("2026-10-08T23:46:00+00:00", kwargs["added_after"])
        self.assertEqual((1, 0), (kwargs["page"], kwargs["page_size"]))      # every matching row, not one page

    def test_add_all_matching_to_list_forwards_the_normalised_range(self) -> None:
        fake = self._fake_db()
        fake.get_list.return_value = {"id": 3}
        fake.add_properties_to_list_from_filter.return_value = 5
        with self.webgui_app.app.test_request_context(
            "/api/lists/3/add-properties", method="POST",
            json={"apns": "all", "filters": {"added_after": "2026-10-08T16:46:00-07:00", "added_before": "garbage", "vpt": "1"}},
        ), patch.object(self.webgui_app, "db", fake):
            response = self.webgui_app.api_lists_add_properties.__wrapped__(3)
        payload = response.get_json() if hasattr(response, "get_json") else response[0].get_json()
        self.assertTrue(payload["success"])
        kwargs = fake.add_properties_to_list_from_filter.call_args.kwargs
        self.assertEqual(("2026-10-08T23:46:00+00:00", ""), (kwargs["added_after"], kwargs["added_before"]))

    def test_list_page_forwards_vacancy_filters(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context(
            "/?ai_verdict=likely_vacant&ai_confidence=70&site_vacant=yes&sort=ai_vacancy_confidence"
        ), patch.object(self.webgui_app, "db", fake), patch.object(
            self.webgui_app, "render_template", return_value="rendered"
        ) as render:
            self.webgui_app.search_page.__wrapped__()
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual(
            ("likely_vacant", "70", "yes", "ai_vacancy_confidence"),
            (kwargs["ai_verdict_filter"], kwargs["ai_confidence_min"], kwargs["site_vacant_filter"], kwargs["sort"]),
        )
        ctx = render.call_args.kwargs
        self.assertEqual(
            ("likely_vacant", "70", "yes"),
            (ctx["ai_verdict_filter"], ctx["ai_confidence_filter"], ctx["site_vacant_filter"]),
        )

    def test_select_all_and_add_to_list_forward_vacancy_filters(self) -> None:
        fake = self._fake_db()
        with self.webgui_app.app.test_request_context("/api/apn_list?ai_verdict=none&site_vacant=no"), patch.object(
            self.webgui_app, "db", fake
        ):
            self.webgui_app.api_apn_list.__wrapped__()
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual(("none", "no"), (kwargs["ai_verdict_filter"], kwargs["site_vacant_filter"]))

        fake.get_list.return_value = {"id": 3}
        fake.add_properties_to_list_from_filter.return_value = 1
        with self.webgui_app.app.test_request_context(
            "/api/lists/3/add-properties", method="POST",
            json={"apns": "all", "filters": {"ai_verdict": "likely_vacant", "ai_confidence": "90"}},
        ), patch.object(self.webgui_app, "db", fake):
            self.webgui_app.api_lists_add_properties.__wrapped__(3)
        kwargs = fake.add_properties_to_list_from_filter.call_args.kwargs
        self.assertEqual(("likely_vacant", "90"), (kwargs["ai_verdict_filter"], kwargs["ai_confidence_min"]))

    def test_add_all_with_county_filter_does_not_pass_unknown_kwargs(self) -> None:
        fake = self._fake_db()
        fake.get_list.return_value = {"id": 3}
        fake.add_properties_to_list.return_value = 0
        with self.webgui_app.app.test_request_context(
            "/api/lists/3/add-properties", method="POST",
            json={"apns": "all", "filters": {"county": "ALAMEDA", "site_vacant": "yes"}},
        ), patch.object(self.webgui_app, "db", fake):
            self.webgui_app.api_lists_add_properties.__wrapped__(3)
        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertNotIn("occupancy_filter", kwargs)
        self.assertEqual("yes", kwargs["site_vacant_filter"])

    def test_list_page_renders_vacancy_columns(self) -> None:
        fake = self._fake_db()
        fake.get_bills_with_parcels_filtered.return_value = ([{
            "apn": "1-2-3", "location_of_property": "1 Main St", "city": "OAKLAND",
            "ai_vacancy_verdict": "vacant", "ai_vacancy_confidence": 0.85, "site_vacant": True,
            "ai_vacant_since": "2024-01-15",
        }], 1)
        import flask
        import jinja2

        app = self.webgui_app.app
        env = app.jinja_env.overlay(loader=jinja2.FileSystemLoader(str(SCAN_DIR / "webgui" / "templates")))

        def render(name, **ctx):
            extra = {"request": flask.request, "session": flask.session, "g": flask.g, "config": app.config}
            return env.get_template(name).render(**extra, **ctx)

        with app.test_request_context("/?ai_verdict=likely_vacant&ai_confidence=80"), patch.object(
            self.webgui_app, "db", fake
        ), patch.object(self.webgui_app, "_parcel_zip_by_apn", return_value={}), patch.object(
            self.webgui_app, "render_template", side_effect=render
        ):
            html = self.webgui_app.search_page.__wrapped__()
        self.assertIn("<th>AI Verdict</th>", html)
        self.assertIn("<th>Vacant For</th>", html)
        self.assertIn('title="Vacant since 2024-01-15"', html)
        self.assertIn("likely vacant", html)
        self.assertIn("85%", html)
        self.assertIn('<option value="80" selected>', html)
        self.assertIn('<option value="likely_vacant" selected>', html)

    def _real_render(self):
        import flask
        import jinja2

        app = self.webgui_app.app
        env = app.jinja_env.overlay(loader=jinja2.FileSystemLoader(str(SCAN_DIR / "webgui" / "templates")))

        def render(name, **ctx):
            extra = {"request": flask.request, "session": flask.session, "g": flask.g, "config": app.config}
            return env.get_template(name).render(**extra, **ctx)

        return render

    def test_gallery_tags_cards_with_the_ai_vacancy_verdict(self) -> None:
        fake = self._fake_db()
        rows = [self._gallery_row(apn, f"{apn} Test St") for apn in ("001", "002", "003", "004")]
        rows[0].update(ai_vacancy_verdict="likely_vacant", ai_vacancy_confidence=0.82, ai_vacant_since="2019")
        rows[1].update(ai_vacancy_verdict="occupied", ai_vacancy_confidence=0.6)   # short alias
        rows[2].update(ai_vacancy_verdict="unknown", ai_vacancy_confidence=0.3)
        fake.get_bills_with_parcels_filtered.return_value = (rows, 250)   # enough for a pager

        with self.webgui_app.app.test_request_context("/gallery?ai_verdict=likely_vacant&sort=ai_vacancy_confidence"), patch.object(
            self.webgui_app, "db", fake
        ), patch.object(self.webgui_app, "_parcel_zip_by_apn", return_value={}), patch.object(
            self.webgui_app, "render_template", side_effect=self._real_render()
        ):
            html = self.webgui_app.gallery_page.__wrapped__()

        kwargs = fake.get_bills_with_parcels_filtered.call_args.kwargs
        self.assertEqual(("likely_vacant", "ai_vacancy_confidence"), (kwargs["ai_verdict_filter"], kwargs["sort"]))
        self.assertEqual(1, html.count('vacancy-tag vacancy-vacant'))
        self.assertEqual(1, html.count('vacancy-tag vacancy-occupied'))
        self.assertIn("Likely Vacant<span class=\"conf\">82%</span>", html)
        self.assertIn("Likely Occupied<span class=\"conf\">60%</span>", html)
        self.assertIn('<option value="likely_vacant" selected>AI: Likely Vacant</option>', html)
        self.assertIn("&amp;ai_verdict=likely_vacant&amp;", html)   # pager keeps the filter
        self.assertEqual(1, html.count('class="card-meta vacancy-length"'))
        self.assertIn("(since 2019)", html)
        self.assertIn('<option value="ai_vacant_since"', html)

    def test_property_detail_shows_the_reasoning_behind_the_verdict(self) -> None:
        row = {
            "apn": "001", "location_of_property": "1 Test St", "city": "OAKLAND", "power_status": "",
            "has_vpt": 0, "delinquent": 0, "row_json": {},
            "ai_vacancy_verdict": "likely_vacant", "ai_vacancy_confidence": 0.9,
            "ai_vacancy_rationale": "Boarded windows and a code case.", "ai_vacancy_updated_at": "2026-10-01T00:00:00Z",
            "ai_vacant_since": "2020-03",
        }
        findings = [
            {"agent": "grokbot-1", "verdict": "likely_vacant", "confidence": 0.9, "created_at": "2026-10-01T00:00:00Z",
             "rationale": "Boarded windows and a code case.", "report_md": "## Notes\nStreet View 2025 shows <plywood>.",
             "evidence": [{"signal": "code_case", "detail": "blight complaint", "observed_at": "2026-09",
                           "source_url": "https://example.com/case"}]},
            {"agent": "grokbot-2", "verdict": "unknown", "confidence": 0.2, "created_at": "2026-08-01T00:00:00Z",
             "rationale": "Nothing conclusive.", "report_md": "", "evidence": []},
        ]
        import research_queue

        with self.webgui_app.app.test_request_context("/property/001"), patch.object(
            self.webgui_app.db, "get_bill_with_parcel", return_value=row
        ), patch.object(self.webgui_app.db, "has_favorite", return_value=False), patch.object(
            research_queue, "get_findings", return_value=findings
        ), patch.object(self.webgui_app, "render_template", side_effect=self._real_render()):
            html = self.webgui_app.property_detail.__wrapped__("001")

        self.assertIn("Boarded windows and a code case.", html)
        self.assertIn("(since 2020-03)", html)
        self.assertIn("blight complaint", html)
        self.assertIn('href="https://example.com/case"', html)
        self.assertIn("Full research notes", html)
        self.assertIn("Street View 2025 shows &lt;plywood&gt;.", html)   # agent text is escaped
        self.assertIn("Earlier research", html)
        self.assertIn("Nothing conclusive.", html)

    def test_property_detail_does_not_attribute_a_stale_finding_to_the_verdict(self) -> None:
        row = {
            "apn": "001", "location_of_property": "1 Test St", "city": "OAKLAND", "power_status": "",
            "has_vpt": 0, "delinquent": 0, "row_json": {},
            "ai_vacancy_verdict": "likely_occupied", "ai_vacancy_confidence": 0.7,
            "ai_vacancy_rationale": "Gemini: recent listing photos show furniture.",
        }
        findings = [{"agent": "grokbot-1", "verdict": "likely_vacant", "confidence": 0.9, "created_at": "2026-08-01",
                     "rationale": "Older agent run.", "report_md": "old notes", "evidence": []}]
        import research_queue

        with self.webgui_app.app.test_request_context("/property/001"), patch.object(
            self.webgui_app.db, "get_bill_with_parcel", return_value=row
        ), patch.object(self.webgui_app.db, "has_favorite", return_value=False), patch.object(
            research_queue, "get_findings", return_value=findings
        ), patch.object(self.webgui_app, "render_template", side_effect=self._real_render()):
            html = self.webgui_app.property_detail.__wrapped__("001")

        self.assertNotIn("Full research notes", html)
        self.assertIn("Earlier research", html)
        self.assertIn("Older agent run.", html)

    def test_vacancy_length_is_derived_from_the_start_date(self) -> None:
        from datetime import date

        length = self.webgui_app._vacancy_length
        today = date(2026, 10, 16)
        self.assertEqual("~7 yr 2 mo", length("2019-08", today))
        self.assertEqual("~7 yr 1 mo", length("2019-08-20", today))   # day not reached yet
        self.assertEqual("~7 yr", length("2019", today))              # year precision stays coarse
        self.assertEqual("~5 mo", length("2026-05", today))
        self.assertEqual("<1 mo", length("2026-10-01", today))
        self.assertEqual("<1 yr", length("2026", today))
        self.assertEqual("~2 yr", length("2024-10", today))
        for bad in ("", None, "2027-01", "2019-13", "since 2019", "2019/08"):
            self.assertEqual("", length(bad, today), bad)

    def test_map_markers_filter_by_ai_verdict_and_carry_vacancy_fields(self) -> None:
        fake = self._fake_db()
        fake.get_bills_with_parcels_filtered.return_value = ([{
            "apn": "1", "lat": 37.8, "lng": -122.2, "location_of_property": "1 Main St", "row_json": {},
            "parcel_number": "", "tracer_number": "", "tax_year": "", "last_payment": "", "delinquent": 0,
            "power_status": "", "has_vpt": 0, "vpt_marker": "", "city": "OAKLAND", "bill_url": "",
            "condition_score": None, "ai_vacancy_verdict": "vacant", "ai_vacancy_confidence": 0.7,
            "ai_vacant_since": "2018",
        }], 1)
        with self.webgui_app.app.test_request_context("/api/markers?ai_verdict=likely_vacant"), patch.object(
            self.webgui_app, "db", fake
        ):
            payload = self.webgui_app.api_markers.__wrapped__().get_json()

        self.assertEqual("likely_vacant", fake.get_bills_with_parcels_filtered.call_args.kwargs["ai_verdict_filter"])
        item = payload["items"][0]
        self.assertEqual(("likely_vacant", 0.7, "2018"),
                         (item["ai_vacancy_verdict"], item["ai_vacancy_confidence"], item["ai_vacant_since"]))
        self.assertTrue(item["ai_vacancy_length"].endswith("yr"))

    def test_route_map_rows_apply_the_ai_verdict_filter(self) -> None:
        match = self.webgui_app._row_matches_map_filters
        common = dict(q="", zip_values=set(), power_filter="", fav_filter="", city_filter="", vpt_filter="",
                      delinquent_filter="", owner_name_filter="", new_filter="", favorites_set=set())
        vacant, occupied, unassessed = ({"ai_vacancy_verdict": "vacant"}, {"ai_vacancy_verdict": "likely_occupied"}, {})
        self.assertTrue(match(vacant, {}, **common, ai_verdict_filter="likely_vacant"))
        self.assertFalse(match(occupied, {}, **common, ai_verdict_filter="likely_vacant"))
        self.assertTrue(match(unassessed, {}, **common, ai_verdict_filter="none"))
        self.assertFalse(match(vacant, {}, **common, ai_verdict_filter="none"))
        self.assertTrue(match(occupied, {}, **common))

    def test_map_template_has_the_ai_verdict_filter(self) -> None:
        source = (SCAN_DIR / "webgui" / "templates" / "map.html").read_text(encoding="utf-8")
        self.assertIn('id="filter-ai-verdict"', source)
        self.assertIn("params.set('ai_verdict', aiVerdict)", source)
        self.assertIn("item.ai_vacancy_length", source)

    def test_list_and_gallery_templates_include_the_date_filter(self) -> None:
        import jinja2

        templates = SCAN_DIR / "webgui" / "templates"
        partial = (templates / "_added_filter.html").read_text(encoding="utf-8")
        jinja2.Environment().parse(partial)
        for marker in ('name="added_after"', 'name="added_before"', "/api/fleet/status", 'id="added-new-since-fleet"',
                       'id="added-before-fleet"', "datetime-local", "toISOString", "DOMContentLoaded"):
            self.assertIn(marker, partial)
        for name in ("index.html", "gallery.html"):
            source = (templates / name).read_text(encoding="utf-8")
            jinja2.Environment().parse(source)
            self.assertIn('{% include "_added_filter.html" %}', source)
            self.assertIn('class="added-at"', source)

    def test_fleet_status_route_returns_overall_and_nodes(self) -> None:
        from tests.fleet_fakes import FakeClient
        client = FakeClient(self._fleet_store())
        with self.webgui_app.app.test_request_context("/api/fleet/status"):
            with patch.object(self.webgui_app.db, "get_client", return_value=client):
                payload = self.webgui_app.api_fleet_status.__wrapped__().get_json()

        self.assertEqual(payload["run_id"], "r1")
        self.assertEqual(payload["overall"]["scanned"], 50)
        self.assertEqual(payload["nodes"][0]["public_ip"], "1.2.3.4")
        self.assertEqual(payload["nodes"][0]["state"], "running")

    def test_fleet_status_route_reports_errors_as_json(self) -> None:
        with self.webgui_app.app.test_request_context("/api/fleet/status"):
            with patch.object(self.webgui_app.db, "get_client", side_effect=RuntimeError("no database")):
                payload = self.webgui_app.api_fleet_status.__wrapped__().get_json()
        self.assertEqual((payload["error"], payload["nodes"], payload["overall"]), ("no database", [], None))

    def test_fleet_stop_and_resume_routes_flip_job_status(self) -> None:
        from tests.fleet_fakes import FakeClient
        store = self._fleet_store()
        client = FakeClient(store)
        with patch.object(self.webgui_app.db, "get_client", return_value=client):
            with self.webgui_app.app.test_request_context("/api/fleet/stop", method="POST"):
                stopped = self.webgui_app.api_fleet_stop.__wrapped__().get_json()
            self.assertEqual((stopped["success"], stopped["changed"]), (True, 1))
            self.assertEqual(store["scanner_jobs"][0]["status"], "interrupted")

            with self.webgui_app.app.test_request_context("/api/fleet/resume", method="POST"):
                resumed = self.webgui_app.api_fleet_resume.__wrapped__().get_json()
            self.assertEqual((resumed["success"], resumed["changed"]), (True, 1))
            self.assertEqual(store["scanner_jobs"][0]["status"], "running")

    def test_scan_template_has_the_fleet_tab_and_parses(self) -> None:
        import jinja2

        source = (SCAN_DIR / "webgui" / "templates" / "scan.html").read_text(encoding="utf-8")
        jinja2.Environment().parse(source)   # raises TemplateSyntaxError on a broken block
        for marker in ('data-panel="panel-fleet"', 'id="panel-fleet"', "/api/fleet/status", "fleetAction("):
            self.assertIn(marker, source)
        self.assertIn("document.getElementById('btn-stop').disabled = false;", source)

    def test_reorder_route_api_cleans_apns_and_delegates_to_db(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/api/lists/7/reorder",
            method="POST",
            json={"apns": [" 003 ", "", "001", None, "002"]},
        ):
            with patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}
            ), patch.object(
                self.webgui_app.db, "reorder_list_properties", return_value=3
            ) as reorder:
                response = self.webgui_app.api_lists_reorder.__wrapped__(7)

        self.assertEqual({"success": True, "count": 3}, response.get_json())
        reorder.assert_called_once_with(7, ["003", "001", "002"])

    def test_route_preview_api_returns_db_preview_payload(self) -> None:
        preview = {
            "total": 2,
            "stops": [
                {"apn": "001", "queue_position": 0, "lat": 37.8, "lng": -122.2},
                {"apn": "002", "queue_position": 1, "lat": 37.9, "lng": -122.3},
            ],
        }
        with self.webgui_app.app.test_request_context("/api/lists/7/route-preview"):
            with patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}
            ), patch.object(
                self.webgui_app.db, "get_list_route_preview", return_value=preview
            ):
                response = self.webgui_app.api_lists_route_preview.__wrapped__(7)

        self.assertEqual(preview, response.get_json())

    def test_search_list_id_scopes_results_to_selected_route(self) -> None:
        rows = [
            self._search_row("001", "1 Test St"),
            self._search_row("002", "2 Test St"),
            self._search_row("003", "3 Test St"),
        ]
        rendered: dict = {}

        def fake_render_template(template_name: str, **context):
            rendered["template_name"] = template_name
            rendered.update(context)
            return "rendered"

        with self.webgui_app.app.test_request_context("/search?list_id=7"):
            with patch.object(self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}), patch.object(
                self.webgui_app.db,
                "get_list_properties",
                return_value=[{"apn": "003"}, {"apn": "001"}],
            ), patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db,
                "get_bills_with_parcels_filtered",
                return_value=(rows, len(rows)),
            ), patch.object(
                self.webgui_app.db, "get_distinct_zips", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_lists", return_value=[{"id": 7, "name": "Route"}]
            ), patch.object(
                self.webgui_app, "_parcel_zip_by_apn", return_value={}
            ), patch.object(
                self.webgui_app, "render_template", side_effect=fake_render_template
            ):
                response = self.webgui_app.search_page.__wrapped__()

        self.assertEqual("rendered", response)
        self.assertEqual(["001", "003"], [row["apn"] for row in rendered["rows"]])
        self.assertEqual(2, rendered["total"])

    def test_search_page_forwards_new_filter(self) -> None:
        rendered: dict = {}

        def fake_render_template(template_name: str, **context):
            rendered["template_name"] = template_name
            rendered.update(context)
            return "rendered"

        with self.webgui_app.app.test_request_context("/search?new=1&vpt=1"):
            with patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db,
                "get_bills_with_parcels_filtered",
                return_value=([], 0),
            ) as filtered, patch.object(
                self.webgui_app.db, "get_distinct_zips", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_lists", return_value=[]
            ), patch.object(
                self.webgui_app, "_parcel_zip_by_apn", return_value={}
            ), patch.object(
                self.webgui_app, "render_template", side_effect=fake_render_template
            ):
                response = self.webgui_app.search_page.__wrapped__()

        self.assertEqual("rendered", response)
        self.assertEqual("1", rendered["new_filter"])
        call_kwargs = filtered.call_args.kwargs
        self.assertEqual("1", call_kwargs["new_filter"])
        self.assertEqual("1", call_kwargs["vpt_filter"])

    def test_gallery_list_id_scopes_results_to_selected_route(self) -> None:
        rows = [
            self._gallery_row("001", "1 Test St"),
            self._gallery_row("002", "2 Test St"),
            self._gallery_row("003", "3 Test St"),
        ]
        rendered: dict = {}

        def fake_render_template(template_name: str, **context):
            rendered["template_name"] = template_name
            rendered.update(context)
            return "rendered"

        with self.webgui_app.app.test_request_context("/gallery?list_id=7"):
            with patch.object(self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}), patch.object(
                self.webgui_app.db,
                "get_list_properties",
                return_value=[{"apn": "003"}, {"apn": "001"}],
            ), patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db,
                "get_bills_with_parcels_filtered",
                return_value=(rows, len(rows)),
            ), patch.object(
                self.webgui_app.db, "get_distinct_zips", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_distinct_cities", return_value=[], create=True
            ), patch.object(
                self.webgui_app.db, "get_lists", return_value=[{"id": 7, "name": "Route"}]
            ), patch.object(
                self.webgui_app, "_parcel_zip_by_apn", return_value={}
            ), patch.object(
                self.webgui_app, "render_template", side_effect=fake_render_template
            ):
                response = self.webgui_app.gallery_page.__wrapped__()

        self.assertEqual("rendered", response)
        self.assertEqual(["001", "003"], [row["apn"] for row in rendered["rows"]])
        self.assertEqual(2, rendered["total"])

    def test_lists_api_does_not_touch_favorites(self) -> None:
        lists = [{"id": 3, "name": "Route"}, {"id": 2, "name": "Favorites"}]
        with self.webgui_app.app.test_request_context("/api/lists"):
            with patch.object(
                self.webgui_app.db, "get_lists", return_value=lists
            ), patch.object(
                self.webgui_app.db, "get_favorites_list_id", return_value=2
            ), patch.object(
                self.webgui_app.db,
                "get_client",
                side_effect=AssertionError("GET /api/lists must not delete favorites"),
            ):
                response = self.webgui_app.api_lists_get.__wrapped__()

        self.assertEqual([{"id": 3, "name": "Route"}], response.get_json())

    def test_favorites_details_returns_properties(self) -> None:
        rows_by_apn = {
            "001": {
                "apn": "001",
                "location": "1 Test St",
                "city": "OAKLAND",
                "has_vpt": 1,
                "zip_code": "94606",
                "owner_name": "Jane Doe",
                "delinquent": 0,
            }
        }

        with self.webgui_app.app.test_request_context("/api/favorites/details"):
            with patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=["001"]
            ), patch.object(
                self.webgui_app, "_map_marker_rows_by_apn", return_value=rows_by_apn
            ), patch.object(
                self.webgui_app, "_parcel_zip_by_apn", return_value={}
            ):
                response = self.webgui_app.api_favorites_details.__wrapped__()

        properties = response.get_json()["properties"]
        self.assertEqual("001", properties[0]["apn"])
        self.assertTrue(properties[0]["is_favorite"])
        self.assertEqual("94606", properties[0]["situs_zip"])
        self.assertEqual("Yes", properties[0]["has_vpt"])
        self.assertEqual("1 Test St", properties[0]["location_of_property"])
        self.assertEqual(0, properties[0]["queue_position"])

    def test_favorites_bulk_remove_delegates_to_db(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/api/favorites/bulk-remove",
            method="POST",
            json={"apns": ["001", " 002 ", "", None]},
        ):
            with patch.object(
                self.webgui_app.db, "bulk_remove_favorites", return_value=2
            ) as remove:
                response = self.webgui_app.api_favorites_bulk_remove.__wrapped__()

        self.assertEqual({"status": "ok", "removed": 2}, response.get_json())
        remove.assert_called_once_with(["001", "002"])

    def test_favorites_bulk_remove_requires_apns(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/api/favorites/bulk-remove", method="POST", json={"apns": []}
        ):
            response, status = self.webgui_app.api_favorites_bulk_remove.__wrapped__()

        self.assertEqual(400, status)

    def test_list_bulk_remove_properties_delegates_to_db(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/api/lists/7/remove-properties",
            method="POST",
            json={"apns": ["001", "002"]},
        ):
            with patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}
            ), patch.object(
                self.webgui_app.db, "bulk_remove_properties_from_list", return_value=2
            ) as remove:
                response = self.webgui_app.api_lists_remove_properties.__wrapped__(7)

        self.assertEqual({"success": True, "count": 2}, response.get_json())
        remove.assert_called_once_with(7, ["001", "002"])

    def test_list_bulk_remove_missing_list_returns_404(self) -> None:
        with self.webgui_app.app.test_request_context(
            "/api/lists/99/remove-properties",
            method="POST",
            json={"apns": ["001"]},
        ):
            with patch.object(self.webgui_app.db, "get_list", return_value=None):
                response, status = self.webgui_app.api_lists_remove_properties.__wrapped__(99)

        self.assertEqual(404, status)

    def test_list_detail_returns_list_view_rows(self) -> None:
        rows_by_apn = {
            "001": {
                "apn": "001",
                "location": "1 Test St",
                "city": "OAKLAND",
                "has_vpt": 0,
                "zip_code": "94606",
            }
        }
        with self.webgui_app.app.test_request_context("/api/lists/7"):
            with patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}
            ), patch.object(
                self.webgui_app.db,
                "get_list_properties",
                return_value=[{"apn": "001", "sort_order": 0}],
            ), patch.object(
                self.webgui_app, "_map_marker_rows_by_apn", return_value=rows_by_apn
            ), patch.object(
                self.webgui_app, "_parcel_zip_by_apn", return_value={}
            ), patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_list_route_preview", return_value={"total": 0, "stops": []}
            ):
                response = self.webgui_app.api_lists_get_one.__wrapped__(7)

        properties = response.get_json()["properties"]
        self.assertEqual("94606", properties[0]["situs_zip"])
        self.assertEqual("No", properties[0]["has_vpt"])

    def test_favorites_list_cannot_be_deleted(self) -> None:
        with self.webgui_app.app.test_request_context("/api/lists/2", method="DELETE"):
            with patch.object(
                self.webgui_app.db, "get_favorites_list_id", return_value=2
            ), patch.object(
                self.webgui_app.db,
                "delete_list",
                side_effect=AssertionError("Favorites must not be deleted"),
            ):
                response, status = self.webgui_app.api_lists_delete.__wrapped__(2)

        self.assertEqual(400, status)

    def test_favorites_export_returns_csv(self) -> None:
        bills = [
            {
                "apn": "001",
                "location_of_property": "1 Test St",
                "city": "OAKLAND",
                "zip_code": "94606",
                "has_vpt": 1,
                "vpt_marker": "MEAS-W",
                "delinquent": 0,
                "power_status": "off",
                "condition_score": 7.5,
                "owner_name": "Jane Doe",
                "research_status": "completed",
                "tax_year": "2024-2025",
                "last_payment": "DEC 10, 2024",
                "bill_url": "https://example.com/bill",
                "lat": 37.8,
                "lng": -122.2,
            }
        ]
        with self.webgui_app.app.test_request_context("/api/favorites/export"):
            with patch.object(
                self.webgui_app.db, "get_favorites_list_id", return_value=2
            ), patch.object(
                self.webgui_app, "_list_apns_in_order", return_value=["001"]
            ), patch.object(
                self.webgui_app, "_chunked_in_query", return_value=bills
            ):
                response = self.webgui_app.api_favorites_export.__wrapped__()

        self.assertEqual("text/csv", response.mimetype)
        self.assertIn("attachment; filename=\"favorites-", response.headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual("APN", rows[0][0])
        self.assertEqual("Zip", rows[0][3])
        self.assertEqual(["001", "1 Test St", "OAKLAND", "94606"], rows[1][:4])
        self.assertEqual("Yes", rows[1][4])
        self.assertEqual("No", rows[1][6])

    def test_list_export_returns_csv_with_list_name(self) -> None:
        with self.webgui_app.app.test_request_context("/api/lists/7/export"):
            with patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "My Route"}
            ), patch.object(
                self.webgui_app, "_list_apns_in_order", return_value=[]
            ), patch.object(
                self.webgui_app, "_chunked_in_query", return_value=[]
            ):
                response = self.webgui_app.api_list_export.__wrapped__(7)

        self.assertIn("attachment; filename=\"my-route-", response.headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(1, len(rows))

    def test_list_export_missing_list_returns_404(self) -> None:
        with self.webgui_app.app.test_request_context("/api/lists/99/export"):
            with patch.object(self.webgui_app.db, "get_list", return_value=None):
                response, status = self.webgui_app.api_list_export.__wrapped__(99)

        self.assertEqual(404, status)

    def test_parcel_zip_lookup_reads_zipcode_key(self) -> None:
        parcels = [
            {"apn": "001", "row_json": {"ZIPCODE": "94606"}},
            {"apn": "002", "row_json": '{"SitusZip": "94607"}'},
            {"apn": "003", "row_json": {}},
        ]
        with patch.object(self.webgui_app, "_chunked_in_query", return_value=parcels):
            zips = self.webgui_app._parcel_zip_by_apn(["001", "002", "003"])

        self.assertEqual({"001": "94606", "002": "94607"}, zips)

    def test_search_page_fills_zip_from_parcel_when_row_has_none(self) -> None:
        rows = [self._search_row("001", "1 Test St")]
        rendered: dict = {}

        def fake_render_template(template_name: str, **context):
            rendered["template_name"] = template_name
            rendered.update(context)
            return "rendered"

        with self.webgui_app.app.test_request_context("/search"):
            with patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db,
                "get_bills_with_parcels_filtered",
                return_value=(rows, 1),
            ), patch.object(
                self.webgui_app.db, "get_distinct_zips", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_lists", return_value=[]
            ), patch.object(
                self.webgui_app,
                "_parcel_zip_by_apn",
                return_value={"001": "94606"},
            ), patch.object(
                self.webgui_app, "render_template", side_effect=fake_render_template
            ):
                self.webgui_app.search_page.__wrapped__()

        self.assertEqual("94606", rendered["rows"][0]["situs_zip"])

    def test_property_detail_falls_back_to_parcel_zipcode(self) -> None:
        row = {
            "apn": "001",
            "location_of_property": "1 Test St",
            "city": "OAKLAND",
            "power_status": "",
            "has_vpt": 0,
            "delinquent": 0,
            "row_json": {"ZIPCODE": "94606"},
        }
        rendered: dict = {}

        def fake_render_template(template_name: str, **context):
            rendered["template_name"] = template_name
            rendered.update(context)
            return "rendered"

        with self.webgui_app.app.test_request_context("/property/001"):
            with patch.object(
                self.webgui_app.db, "get_bill_with_parcel", return_value=row
            ), patch.object(
                self.webgui_app.db, "has_favorite", return_value=False
            ), patch.object(
                self.webgui_app, "render_template", side_effect=fake_render_template
            ):
                self.webgui_app.property_detail.__wrapped__("001")

        self.assertEqual("94606", rendered["property"]["situs_zip"])

    def test_map_new_filter_uses_added_at_window(self) -> None:
        now = datetime.now(timezone.utc)
        base_kwargs = dict(
            q="",
            zip_values=set(),
            power_filter="",
            fav_filter="",
            city_filter="",
            vpt_filter="",
            delinquent_filter="",
            owner_name_filter="",
            favorites_set=set(),
        )
        recent = {"apn": "001", "added_at": (now - timedelta(days=3)).isoformat()}
        old = {"apn": "002", "added_at": (now - timedelta(days=90)).isoformat()}
        missing = {"apn": "003", "added_at": None}

        self.assertTrue(
            self.webgui_app._row_matches_map_filters(recent, {}, new_filter="1", **base_kwargs)
        )
        self.assertFalse(
            self.webgui_app._row_matches_map_filters(old, {}, new_filter="1", **base_kwargs)
        )
        self.assertFalse(
            self.webgui_app._row_matches_map_filters(missing, {}, new_filter="1", **base_kwargs)
        )
        self.assertTrue(
            self.webgui_app._row_matches_map_filters(old, {}, new_filter="", **base_kwargs)
        )

    def test_map_zip_filter_uses_row_zip_code(self) -> None:
        base_kwargs = dict(
            q="",
            power_filter="",
            fav_filter="",
            city_filter="",
            vpt_filter="",
            delinquent_filter="",
            owner_name_filter="",
            new_filter="",
            favorites_set=set(),
        )
        row = {"apn": "001", "zip_code": "94606", "row_json": {}}

        self.assertTrue(
            self.webgui_app._row_matches_map_filters(row, {}, zip_values={"94606"}, **base_kwargs)
        )
        self.assertFalse(
            self.webgui_app._row_matches_map_filters(row, {}, zip_values={"94704"}, **base_kwargs)
        )

    def test_markers_api_forwards_new_filter(self) -> None:
        with self.webgui_app.app.test_request_context("/api/markers?new=1&vpt=1"):
            with patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db,
                "get_bills_with_parcels_filtered",
                return_value=([], 0),
            ) as filtered:
                response = self.webgui_app.api_markers.__wrapped__()

        self.assertEqual(200, response.status_code)
        call_kwargs = filtered.call_args.kwargs
        self.assertEqual("1", call_kwargs["new_filter"])
        self.assertEqual("1", call_kwargs["vpt_filter"])

    def test_markers_api_filters_list_rows_by_new(self) -> None:
        now = datetime.now(timezone.utc)
        rows = [self._search_row("001", "1 Test St"), self._search_row("002", "2 Test St")]
        rows[0].update({"added_at": (now - timedelta(days=2)).isoformat(), "lat": 37.8, "lng": -122.2})
        rows[1].update({"added_at": (now - timedelta(days=200)).isoformat(), "lat": 37.9, "lng": -122.3})

        with self.webgui_app.app.test_request_context("/api/markers?new=1&list_id=7"):
            with patch.object(
                self.webgui_app.db, "get_favorites_apns", return_value=[]
            ), patch.object(
                self.webgui_app.db, "get_list", return_value={"id": 7, "name": "Route"}
            ), patch.object(
                self.webgui_app, "_get_list_map_rows", return_value=rows
            ):
                response = self.webgui_app.api_markers.__wrapped__()

        self.assertEqual(["001"], [item["apn"] for item in response.get_json()["items"]])

    @staticmethod
    def _search_row(apn: str, address: str) -> dict:
        return {
            "apn": apn,
            "added_at": None,
            "pdf_file": "",
            "bill_url": "",
            "parcel_number": "",
            "tracer_number": "",
            "location_of_property": address,
            "tax_year": "",
            "last_payment": "",
            "delinquent": 0,
            "power_status": "",
            "has_vpt": 0,
            "vpt_marker": "",
            "city": "OAKLAND",
            "condition_score": None,
            "condition_notes": "",
            "streetview_image_path": "",
            "property_search_url": "",
            "mailing_search_url": "",
            "situs_zip": "",
            "owner_name": "",
            "important_notes": "",
            "outreach_score": None,
            "outreach_stage": "",
            "row_json": {},
        }

    @classmethod
    def _gallery_row(cls, apn: str, address: str) -> dict:
        row = cls._search_row(apn, address)
        row.update(
            {
                "prop_last_sale_date": "",
                "deceased_count": None,
                "prop_occupancy_type": "",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
