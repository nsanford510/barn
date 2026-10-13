from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

import research_queue


def _client() -> MagicMock:
    """Supabase client mock whose chained builder calls all return the same builder."""
    builder = MagicMock()
    for method in ("upsert", "update", "select", "eq", "in_", "order", "limit"):
        getattr(builder, method).return_value = builder
    builder.execute.return_value = MagicMock(data=[], count=0)
    client = MagicMock()
    client.table.return_value = builder
    client.builder = builder
    return client


class TokenTests(unittest.TestCase):
    def test_hash_is_hex_sha256_of_the_token(self) -> None:
        self.assertEqual(hashlib.sha256(b"abc").hexdigest(), research_queue.hash_token("abc"))

    def test_add_agent_stores_only_the_hash(self) -> None:
        client = _client()
        with patch.object(research_queue.db, "get_client", return_value=client):
            token = research_queue.add_agent(" grokbot-1 ")

        payload = client.builder.upsert.call_args[0][0]
        self.assertEqual("grokbot-1", payload["name"])
        self.assertEqual(research_queue.hash_token(token), payload["token_hash"])
        self.assertTrue(payload["active"])
        self.assertNotIn(token, payload.values())
        self.assertEqual("name", client.builder.upsert.call_args[1]["on_conflict"])

    def test_add_agent_requires_a_name(self) -> None:
        with self.assertRaises(ValueError):
            research_queue.add_agent("  ")

    def test_revoke_reports_whether_an_agent_matched(self) -> None:
        client = _client()
        with patch.object(research_queue.db, "get_client", return_value=client):
            self.assertFalse(research_queue.revoke_agent("nobody"))
            client.builder.execute.return_value = MagicMock(data=[{"name": "grokbot-1"}])
            self.assertTrue(research_queue.revoke_agent("grokbot-1"))
        client.builder.update.assert_called_with({"active": False})


class EnqueueTests(unittest.TestCase):
    def test_rows_are_ordered_favorites_then_power_off_then_rest(self) -> None:
        bills = [
            {"apn": "3", "power_status": "on"},
            {"apn": "2", "power_status": "OFF"},
            {"apn": "1", "power_status": None},
            {"apn": "4", "power_status": "on"},
            {"apn": "2", "power_status": "off"},
            {"apn": None},
        ]
        rows = research_queue.build_task_rows(bills, favorites={"4"})
        self.assertEqual(["4", "2", "1", "3"], [row["apn"] for row in rows])
        self.assertEqual([2, 1, 0, 0], [row["priority"] for row in rows])

    def test_limit_keeps_the_highest_priority_rows(self) -> None:
        bills = [{"apn": "1"}, {"apn": "2", "power_status": "off"}, {"apn": "3"}]
        rows = research_queue.build_task_rows(bills, favorites=set(), limit=2)
        self.assertEqual(["2", "1"], [row["apn"] for row in rows])

    def test_enqueue_skips_bills_that_already_have_a_task(self) -> None:
        client = _client()

        def select_all(table, columns, apply=None, page_size=1000):
            if table == "bills":
                return [{"apn": "1", "power_status": "off"}, {"apn": "2", "power_status": "on"}]
            return [{"apn": "2"}]

        with patch.object(research_queue.db, "get_client", return_value=client), \
                patch.object(research_queue.db, "_select_all", side_effect=select_all), \
                patch.object(research_queue.db, "get_favorites_apns", return_value=[]):
            added = research_queue.enqueue(city="OAKLAND")

        self.assertEqual(1, added)
        self.assertEqual([{"apn": "1", "priority": 1}], client.builder.upsert.call_args[0][0])
        self.assertTrue(client.builder.upsert.call_args[1]["ignore_duplicates"])

    def test_requeue_resets_the_claim_and_attempts(self) -> None:
        client = _client()
        client.builder.execute.return_value = MagicMock(data=[{"apn": "1"}, {"apn": "2"}])
        with patch.object(research_queue.db, "get_client", return_value=client):
            self.assertEqual(2, research_queue.requeue("failed"))

        payload = client.builder.update.call_args[0][0]
        self.assertEqual("pending", payload["status"])
        self.assertEqual(0, payload["attempts"])
        self.assertIsNone(payload["claim_token"])
        client.builder.eq.assert_called_with("status", "failed")

    def test_requeue_refuses_other_statuses(self) -> None:
        with self.assertRaises(ValueError):
            research_queue.requeue("claimed")


class StatusTests(unittest.TestCase):
    def test_summarize_reports_progress_agents_and_expired_leases(self) -> None:
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        result = research_queue.summarize(
            {"pending": 5, "claimed": 2, "done": 3},
            {"likely_vacant": 2, "unknown": 1},
            [
                {"name": "grokbot-2", "active": False, "last_seen_at": None},
                {"name": "grokbot-1", "active": True, "last_seen_at": "2026-10-13T11:59:00+00:00"},
            ],
            [
                {"apn": "1", "claimed_by": "grokbot-1", "lease_expires_at": "2026-10-13T12:30:00+00:00"},
                {"apn": "2", "claimed_by": "grokbot-1", "lease_expires_at": "2026-10-13T11:00:00Z"},
            ],
            now=now,
        )

        self.assertEqual({"pending": 5, "claimed": 2, "done": 3, "failed": 0}, result["tasks"])
        self.assertEqual(10, result["total"])
        self.assertEqual(30.0, result["pct"])
        self.assertEqual(1, result["expired_leases"])
        self.assertEqual({"likely_vacant": 2, "likely_occupied": 0, "unknown": 1}, result["verdicts"])
        first, second = result["agents"]
        self.assertEqual("grokbot-1", first["name"])
        self.assertEqual(60, first["last_seen_age_s"])
        self.assertEqual([False, True], [claim["expired"] for claim in first["claims"]])
        self.assertFalse(second["active"])
        self.assertIsNone(second["last_seen_age_s"])
        self.assertEqual([], second["claims"])

    def test_summarize_handles_an_empty_queue(self) -> None:
        result = research_queue.summarize({}, {}, [], [])
        self.assertEqual(0, result["total"])
        self.assertEqual(0.0, result["pct"])


class FindingsTests(unittest.TestCase):
    def test_evidence_links_are_kept_only_for_http_urls(self) -> None:
        cleaned = research_queue._clean_evidence([
            {"signal": "boarded", "detail": "all windows", "source_url": "https://example.com/a", "observed_at": "2026-03"},
            {"signal": "bad link", "source_url": "javascript:alert(1)"},
            "not an object",
        ])
        self.assertEqual(2, len(cleaned))
        self.assertEqual("https://example.com/a", cleaned[0]["source_url"])
        self.assertEqual("", cleaned[1]["source_url"])
        self.assertEqual([], research_queue._clean_evidence(None))

    def test_get_findings_cleans_evidence(self) -> None:
        client = _client()
        client.builder.execute.return_value = MagicMock(
            data=[{"agent": "grokbot-1", "verdict": "unknown", "evidence": [{"source_url": "ftp://x"}]}]
        )
        with patch.object(research_queue.db, "get_client", return_value=client):
            findings = research_queue.get_findings("1-2-3")

        client.table.assert_called_with("vacancy_findings")
        client.builder.eq.assert_called_with("apn", "1-2-3")
        self.assertEqual("", findings[0]["evidence"][0]["source_url"])


class CliTests(unittest.TestCase):
    def test_enqueue_arguments_reach_enqueue(self) -> None:
        with patch.object(research_queue, "enqueue", return_value=3) as enqueue, patch("builtins.print"):
            code = research_queue.main(["enqueue", "--city", "OAKLAND", "--apns", "1, 2,", "--limit", "5"])
        self.assertEqual(0, code)
        enqueue.assert_called_once_with(city="OAKLAND", apns=["1", "2"], limit=5)

    def test_revoking_an_unknown_agent_fails(self) -> None:
        with patch.object(research_queue, "revoke_agent", return_value=False), patch("builtins.print"):
            self.assertEqual(1, research_queue.main(["revoke-agent", "nobody"]))

    def test_add_agent_prints_the_token(self) -> None:
        with patch.object(research_queue, "add_agent", return_value="tok-123"), patch("builtins.print") as out:
            research_queue.main(["add-agent", "grokbot-1"])
        self.assertIn("tok-123", " ".join(str(call.args[0]) for call in out.call_args_list))


if __name__ == "__main__":
    unittest.main()
