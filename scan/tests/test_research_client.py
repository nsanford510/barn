from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

SCAN_DIR = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "research_client", SCAN_DIR / "research_agent_kit" / "research_client.py"
)
research_client = importlib.util.module_from_spec(_spec)
sys.modules["research_client"] = research_client
_spec.loader.exec_module(research_client)

ENV = {
    "BARN_SUPABASE_URL": "https://example.supabase.co/",
    "BARN_SUPABASE_ANON_KEY": "sb_publishable_abc",
    "BARN_AGENT_TOKEN": "agent-token",
}


def _response(body: str) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body.encode("utf-8")
    response.__enter__.return_value = response
    return response


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example", code, "refused", {}, io.BytesIO(body.encode("utf-8")))


@patch.dict(os.environ, ENV, clear=False)
class RequestTests(unittest.TestCase):
    def test_request_targets_the_rpc_and_carries_the_agent_token(self) -> None:
        request = research_client.build_request("claim_vacancy_research", {"p_city": "OAKLAND"})

        self.assertEqual("https://example.supabase.co/rest/v1/rpc/claim_vacancy_research", request.full_url)
        self.assertEqual("POST", request.get_method())
        self.assertEqual("sb_publishable_abc", request.get_header("Apikey"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual({"p_agent_token": "agent-token", "p_city": "OAKLAND"}, json.loads(request.data))

    def test_legacy_jwt_key_is_also_sent_as_bearer(self) -> None:
        with patch.dict(os.environ, {"BARN_SUPABASE_ANON_KEY": "eyJabc"}):
            request = research_client.build_request("claim_vacancy_research", {})
        self.assertEqual("Bearer eyJabc", request.get_header("Authorization"))

    def test_missing_environment_is_reported(self) -> None:
        with patch.dict(os.environ, {"BARN_AGENT_TOKEN": ""}):
            with self.assertRaisesRegex(research_client.ClientError, "BARN_AGENT_TOKEN"):
                research_client.build_request("claim_vacancy_research", {})

    def test_claim_returns_none_when_the_queue_is_empty(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response("null")):
            self.assertIsNone(research_client.claim())

    def test_claim_sends_zip_codes_only_when_given(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response("null")) as urlopen:
            research_client.claim()
            research_client.claim(zip_codes=["94601", "94607"])

        plain, targeted = (json.loads(c[0][0].data) for c in urlopen.call_args_list)
        self.assertNotIn("p_zip_codes", plain)
        self.assertEqual(["94601", "94607"], targeted["p_zip_codes"])

    def test_claim_sends_exact_apn_only_when_given(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response("null")) as urlopen:
            research_client.claim()
            research_client.claim(apn=" 001 011501309 ")

        plain, targeted = (json.loads(c[0][0].data) for c in urlopen.call_args_list)
        self.assertNotIn("p_apn", plain)
        self.assertEqual("001 011501309", targeted["p_apn"])

    def test_zip_codes_are_flattened_and_deduplicated(self) -> None:
        self.assertEqual(
            ["94601", "94607", "94621"],
            research_client.parse_zip_codes(["94601, 94607", "94621-1234", "94601"]),
        )
        self.assertEqual([], research_client.parse_zip_codes(None))

    def test_cli_rejects_a_malformed_zip_without_calling_the_server(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen") as urlopen, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = research_client.main(["claim", "--zip", "9460"])
        self.assertEqual(1, code)
        self.assertIn("9460", stderr.getvalue())
        urlopen.assert_not_called()

    def test_cli_zip_flags_reach_the_rpc(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response("null")) as urlopen, \
                patch("sys.stdout", new_callable=io.StringIO):
            code = research_client.main(["claim", "--zip", "94601,94607", "--zip", "94621"])
        self.assertEqual(0, code)
        self.assertEqual(["94601", "94607", "94621"], json.loads(urlopen.call_args[0][0].data)["p_zip_codes"])

    def test_cli_apn_flag_reaches_the_rpc(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response("null")) as urlopen, \
                patch("sys.stdout", new_callable=io.StringIO):
            code = research_client.main(["claim", "--apn", "001 011501309"])
        self.assertEqual(0, code)
        self.assertEqual("001 011501309", json.loads(urlopen.call_args[0][0].data)["p_apn"])

    def test_submit_maps_the_finding_to_rpc_parameters(self) -> None:
        finding = {
            "claim_token": "c-1", "verdict": "unknown", "confidence": 0.2,
            "rationale": "nothing found", "report_md": "notes",
        }
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response('{"apn": "1"}')) as urlopen:
            result = research_client.submit(finding)

        self.assertEqual({"apn": "1"}, result)
        body = json.loads(urlopen.call_args[0][0].data)
        self.assertEqual("c-1", body["p_claim_token"])
        self.assertEqual("unknown", body["p_verdict"])
        self.assertEqual(0.2, body["p_confidence"])
        self.assertEqual([], body["p_evidence"])
        self.assertEqual("notes", body["p_report_md"])

    def test_submit_without_a_claim_token_never_calls_the_server(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(research_client.ClientError):
                research_client.submit({"verdict": "unknown"})
        urlopen.assert_not_called()

    def test_server_refusal_surfaces_its_message(self) -> None:
        error = _http_error(403, '{"code": "42501", "message": "Unknown or revoked agent token"}')
        with patch.object(research_client.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(research_client.ClientError, "HTTP 403.*Unknown or revoked agent token"):
                research_client.claim()

    def test_cli_exits_nonzero_on_refusal(self) -> None:
        error = _http_error(400, "not json")
        with patch.object(research_client.urllib.request, "urlopen", side_effect=error), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = research_client.main(["release", "--token", "c-1", "--reason", "blocked"])
        self.assertEqual(1, code)
        self.assertIn("not json", stderr.getvalue())

    def test_cli_claim_prints_json(self) -> None:
        with patch.object(research_client.urllib.request, "urlopen", return_value=_response('{"apn": "1-2"}')), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = research_client.main(["claim", "--city", "OAKLAND"])
        self.assertEqual(0, code)
        self.assertEqual({"apn": "1-2"}, json.loads(stdout.getvalue()))


if __name__ == "__main__":
    unittest.main()
