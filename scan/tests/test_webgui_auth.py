from __future__ import annotations

import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_queue
from webgui_auth_helpers import signed_in_client

STATUS_URL = "/api/vacancy-research/status"


def _load_webgui_app(module_name: str = "test_webgui_app_auth") -> ModuleType:
    module_path = SCAN_DIR / "webgui" / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _queue_status():
    return research_queue.summarize({"pending": 1}, {}, [], [])


class WebguiAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_webgui_app()

    def setUp(self) -> None:
        patcher = patch.object(research_queue, "status", side_effect=_queue_status)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- signed out -------------------------------------------------------

    def test_signed_out_page_redirects_to_login_with_next(self) -> None:
        response = self.mod.app.test_client().get("/search?q=oak")
        self.assertEqual(302, response.status_code)
        self.assertIn("/login?next=", response.headers["Location"])
        self.assertIn("search", response.headers["Location"])

    def test_signed_out_api_returns_401_json(self) -> None:
        response = self.mod.app.test_client().get(STATUS_URL)
        self.assertEqual(401, response.status_code)
        self.assertEqual("Authentication required", response.get_json()["error"])

    def test_public_endpoints_stay_open(self) -> None:
        client = self.mod.app.test_client()
        self.assertEqual(200, client.get("/ping").status_code)
        self.assertEqual(200, client.get("/login").status_code)
        self.assertEqual(200, client.get("/reset-password").status_code)

    def test_every_route_is_protected_unless_allowlisted(self) -> None:
        client = self.mod.app.test_client()
        for rule in self.mod.app.url_map.iter_rules():
            if rule.endpoint in self.mod.PUBLIC_ENDPOINTS or "GET" not in rule.methods:
                continue
            url = re.sub(r"<[^>]+>", "1", rule.rule)
            with self.subTest(endpoint=rule.endpoint):
                self.assertIn(client.get(url).status_code, (302, 401), url)

    # --- /auth/callback ---------------------------------------------------

    def _callback(self, client, claims, role, body=None):
        with patch.object(self.mod, "_supabase_configured", return_value=True), \
             patch.object(self.mod, "_get_claims_from_token", return_value=claims), \
             patch.object(self.mod, "_lookup_app_role", return_value=role):
            return client.post("/auth/callback", json=body if body is not None else {"access_token": "jwt"})

    def test_callback_rejects_missing_and_invalid_tokens(self) -> None:
        client = self.mod.app.test_client()
        self.assertEqual(400, self._callback(client, None, None, body={}).status_code)
        self.assertEqual(401, self._callback(client, None, None).status_code)

    def test_callback_rejects_signed_in_user_without_a_role(self) -> None:
        client = self.mod.app.test_client()
        response = self._callback(client, {"sub": "u1", "email": "a@b.c"}, None)
        self.assertEqual(403, response.status_code)
        self.assertIn("approved", response.get_json()["error"])
        self.assertEqual(401, client.get(STATUS_URL).status_code)

    def test_callback_starts_session_for_approved_user(self) -> None:
        client = self.mod.app.test_client()
        response = self._callback(client, {"sub": "u1", "email": "a@b.c"}, "scout")
        self.assertEqual(200, response.status_code)
        with client.session_transaction() as sess:
            self.assertEqual("scout", sess["role"])
            self.assertNotIn("supabase_access_token", sess)
            self.assertTrue(sess.permanent)
        self.assertEqual(200, client.get(STATUS_URL).status_code)

    def test_callback_only_redirects_to_same_site_paths(self) -> None:
        client = self.mod.app.test_client()
        claims = {"sub": "u1"}
        ok = self._callback(client, claims, "scout", {"access_token": "jwt", "next": "/map?city=OAKLAND"})
        self.assertEqual("/map?city=OAKLAND", ok.get_json()["redirect"])
        for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
            res = self._callback(client, claims, "scout", {"access_token": "jwt", "next": evil})
            self.assertEqual("/search", res.get_json()["redirect"], evil)

    def test_logout_clears_session(self) -> None:
        client = signed_in_client(self.mod)
        self.assertEqual(200, client.get(STATUS_URL).status_code)
        client.get("/logout")
        self.assertEqual(401, client.get(STATUS_URL).status_code)

    # --- roles ------------------------------------------------------------

    def test_scout_cannot_use_admin_endpoints(self) -> None:
        client = signed_in_client(self.mod, role="scout")
        response = client.post("/api/property/bulk-delete", json={"apns": []})
        self.assertEqual(403, response.status_code)

    def test_admin_passes_admin_check(self) -> None:
        client = signed_in_client(self.mod, role="admin")
        # Empty list fails validation after the auth check, without touching the DB.
        response = client.post("/api/property/bulk-delete", json={"apns": []})
        self.assertEqual(400, response.status_code)

    def test_stale_session_is_signed_out_when_role_is_revoked(self) -> None:
        client = signed_in_client(self.mod)
        with client.session_transaction() as sess:
            sess["role_checked_at"] = 0
        with patch.object(self.mod, "_lookup_app_role", return_value=None):
            self.assertEqual(401, client.get(STATUS_URL).status_code)
        self.assertEqual(401, client.get(STATUS_URL).status_code)

    def test_stale_session_survives_a_db_error_during_recheck(self) -> None:
        client = signed_in_client(self.mod)
        with client.session_transaction() as sess:
            sess["role_checked_at"] = 0
        with patch.object(self.mod, "_lookup_app_role", side_effect=RuntimeError("timeout")):
            self.assertEqual(200, client.get(STATUS_URL).status_code)

    def test_bearer_token_from_approved_user_is_accepted(self) -> None:
        client = self.mod.app.test_client()
        with patch.object(self.mod, "_supabase_configured", return_value=True), \
             patch.object(self.mod, "_get_claims_from_token", return_value={"sub": "u1"}), \
             patch.object(self.mod, "_lookup_app_role", return_value="scout"):
            ok = client.get(STATUS_URL, headers={"Authorization": "Bearer jwt"})
        self.assertEqual(200, ok.status_code)
        with patch.object(self.mod, "_supabase_configured", return_value=True), \
             patch.object(self.mod, "_get_claims_from_token", return_value={"sub": "u1"}), \
             patch.object(self.mod, "_lookup_app_role", return_value=None):
            denied = client.get(STATUS_URL, headers={"Authorization": "Bearer jwt"})
        self.assertEqual(401, denied.status_code)

    def test_lookup_prefers_admin_over_scout(self) -> None:
        class _Query:
            def __init__(self, rows): self.rows = rows
            def __getattr__(self, _name): return lambda *a, **k: self
            def execute(self): return type("R", (), {"data": self.rows})()

        class _Client:
            def __init__(self, rows): self.rows = rows
            def table(self, _name): return _Query(self.rows)

        with patch.object(self.mod.db, "get_client", return_value=_Client([{"role": "scout"}, {"role": "admin"}])):
            self.assertEqual("admin", self.mod._lookup_app_role("u1"))
        with patch.object(self.mod.db, "get_client", return_value=_Client([{"role": "user"}])):
            self.assertIsNone(self.mod._lookup_app_role("u1"))

    # --- webhook and config ----------------------------------------------

    def test_webhook_fails_closed_without_a_configured_secret(self) -> None:
        client = self.mod.app.test_client()
        with patch.object(self.mod.db, "get_outreach_setting", return_value=""):
            response = client.post("/api/outreach/webhook/reply", json={"apn": "1"},
                                   headers={"X-Webhook-Secret": ""})
        self.assertEqual(401, response.status_code)
        with patch.object(self.mod.db, "get_outreach_setting", return_value="s3cret"):
            wrong = client.post("/api/outreach/webhook/reply", json={"apn": "1"},
                                headers={"X-Webhook-Secret": "nope"})
        self.assertEqual(401, wrong.status_code)

    def test_production_refuses_default_secret_key(self) -> None:
        with patch.dict(os.environ, {"VERCEL": "1", "SECRET_KEY": ""}):
            with self.assertRaises(RuntimeError):
                _load_webgui_app("test_webgui_app_auth_vercel")


if __name__ == "__main__":
    unittest.main()
