from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

import worker_state


# ---------------------------------------------------------------------------
# Helpers to build fake Supabase chained-builder responses
# ---------------------------------------------------------------------------

def _make_response(data: list) -> MagicMock:
    resp = MagicMock()
    resp.data = data
    return resp


# ---------------------------------------------------------------------------
# Proper mock client using MagicMock for precise call inspection
# ---------------------------------------------------------------------------

def _build_client_mock(table_response_data: list | None = None) -> MagicMock:
    """
    Builds a MagicMock Supabase client where table(...) returns a builder mock.
    All chained methods return the same builder mock, and .execute() returns
    a response with the given data.
    """
    if table_response_data is None:
        table_response_data = []

    response = MagicMock()
    response.data = table_response_data

    # Build a builder that supports arbitrary chaining
    builder = MagicMock()
    # Every method on builder returns builder itself (for chaining)
    builder.select.return_value = builder
    builder.insert.return_value = builder
    builder.upsert.return_value = builder
    builder.update.return_value = builder
    builder.eq.return_value = builder
    builder.is_.return_value = builder
    builder.order.return_value = builder
    builder.limit.return_value = builder
    builder.execute.return_value = response

    client = MagicMock()
    client.table.return_value = builder

    return client, builder, response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHeartbeat(unittest.TestCase):
    """heartbeat() against an in-memory client: scanner_workers has no unique constraint on name."""

    def _db(self):
        sys.path.insert(0, str(SCAN_DIR / "tests"))
        from fleet_fakes import FakeClient
        return FakeClient({})

    def test_first_heartbeat_registers_the_worker_as_online(self):
        client = self._db()
        with patch("db.get_client", return_value=client):
            worker_id = worker_state.heartbeat("my-worker", tunnel_url="https://tunnel.example.com")

        rows = client.store["scanner_workers"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], worker_id)
        self.assertEqual(
            {k: rows[0][k] for k in ("name", "status", "last_heartbeat", "tunnel_url")},
            {"name": "my-worker", "status": "online", "last_heartbeat": "now()", "tunnel_url": "https://tunnel.example.com"},
        )

    def test_later_heartbeats_refresh_the_same_row(self):
        client = self._db()
        with patch("db.get_client", return_value=client):
            first = worker_state.heartbeat("my-worker")
            client.store["scanner_workers"][0]["status"] = "offline"
            second = worker_state.heartbeat("my-worker")
            other = worker_state.heartbeat("another-worker")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(client.store["scanner_workers"]), 2)
        self.assertEqual(client.store["scanner_workers"][0]["status"], "online")

    def test_heartbeat_without_tunnel_url_omits_key_and_keeps_existing_one(self):
        client = self._db()
        with patch("db.get_client", return_value=client):
            worker_state.heartbeat("my-worker", tunnel_url="https://t.example.com")
            worker_state.heartbeat("my-worker")
            first_row = client.store["scanner_workers"][0]
            self.assertEqual(first_row["tunnel_url"], "https://t.example.com")

            fresh = self._db()
        with patch("db.get_client", return_value=fresh):
            worker_state.heartbeat("w2")
        self.assertNotIn("tunnel_url", fresh.store["scanner_workers"][0])

    def test_heartbeat_raises_when_registration_returns_nothing(self):
        client, builder, response = _build_client_mock([])
        with patch("db.get_client", return_value=client):
            with self.assertRaises(RuntimeError):
                worker_state.heartbeat("worker-x")


class TestCreateJob(unittest.TestCase):
    def test_insert_called_with_correct_data_returns_job_id(self):
        job_id = "job-uuid-9999"
        client, builder, response = _build_client_mock([{"id": job_id}])

        with patch("db.get_client", return_value=client):
            result = worker_state.create_job(
                "worker-123",
                "vpt_scan",
                {"cities": ["OAKLAND"]},
            )

        client.table.assert_called_once_with("scanner_jobs")
        builder.insert.assert_called_once_with(
            {
                "worker_id": "worker-123",
                "job_type": "vpt_scan",
                "scope": {"cities": ["OAKLAND"]},
                "status": "running",
            }
        )
        self.assertEqual(result, job_id)

    def test_status_is_always_running(self):
        client, builder, response = _build_client_mock([{"id": "j1"}])

        with patch("db.get_client", return_value=client):
            worker_state.create_job("w1", "pge_scan", {})

        insert_payload = builder.insert.call_args[0][0]
        self.assertEqual(insert_payload["status"], "running")


class TestUpdateJob(unittest.TestCase):
    def test_only_provided_kwargs_included_in_patch(self):
        client, builder, response = _build_client_mock([{"id": "j1"}])

        with patch("db.get_client", return_value=client):
            worker_state.update_job("job-abc", status="completed", processed_count=42)

        builder.update.assert_called_once_with(
            {"status": "completed", "processed_count": 42}
        )
        builder.eq.assert_called_once_with("id", "job-abc")

    def test_none_kwargs_explicitly_clears_field(self):
        """Passing None explicitly clears a field (sentinel pattern)."""
        client, builder, response = _build_client_mock([{"id": "j1"}])

        with patch("db.get_client", return_value=client):
            worker_state.update_job(
                "job-abc",
                status="interrupted",
                current_city=None,
                current_apn=None,
                hit_count=5,
            )

        patch_dict = builder.update.call_args[0][0]
        self.assertIn("status", patch_dict)
        self.assertIn("hit_count", patch_dict)
        # Explicitly passing None should include the field with None value
        self.assertIn("current_city", patch_dict)
        self.assertIsNone(patch_dict["current_city"])
        self.assertIn("current_apn", patch_dict)
        self.assertIsNone(patch_dict["current_apn"])

    def test_all_kwargs_included_when_provided(self):
        client, builder, response = _build_client_mock([{"id": "j1"}])

        with patch("db.get_client", return_value=client):
            worker_state.update_job(
                "job-xyz",
                status="running",
                current_city="OAKLAND",
                current_apn="123-456-789",
                processed_count=100,
                hit_count=10,
                error_summary="some error",
            )

        patch_dict = builder.update.call_args[0][0]
        self.assertEqual(patch_dict["status"], "running")
        self.assertEqual(patch_dict["current_city"], "OAKLAND")
        self.assertEqual(patch_dict["current_apn"], "123-456-789")
        self.assertEqual(patch_dict["processed_count"], 100)
        self.assertEqual(patch_dict["hit_count"], 10)
        self.assertEqual(patch_dict["error_summary"], "some error")

    def test_omitted_kwargs_not_included(self):
        """Omitting a parameter (not passing it) means don't touch that field."""
        client, builder, response = _build_client_mock([{"id": "j1"}])

        with patch("db.get_client", return_value=client):
            worker_state.update_job(
                "job-abc",
                status="completed",
                hit_count=5,
            )

        patch_dict = builder.update.call_args[0][0]
        self.assertIn("status", patch_dict)
        self.assertIn("hit_count", patch_dict)
        self.assertNotIn("current_city", patch_dict)
        self.assertNotIn("current_apn", patch_dict)
        self.assertNotIn("processed_count", patch_dict)

    def test_no_db_call_when_no_kwargs(self):
        client, builder, response = _build_client_mock([])

        with patch("db.get_client", return_value=client):
            worker_state.update_job("job-empty")

        client.table.assert_not_called()


class TestWriteCheckpoint(unittest.TestCase):
    def test_insert_called_with_correct_data(self):
        client, builder, response = _build_client_mock([{"id": "cp1"}])

        with patch("db.get_client", return_value=client):
            worker_state.write_checkpoint(
                "job-123",
                city="OAKLAND",
                apn="001-002-003",
                row_offset=50,
                phase="scanning",
                extra={"foo": "bar"},
            )

        client.table.assert_called_once_with("scanner_job_checkpoints")
        builder.insert.assert_called_once_with(
            {
                "job_id": "job-123",
                "city": "OAKLAND",
                "apn": "001-002-003",
                "row_offset": 50,
                "phase": "scanning",
                "extra": {"foo": "bar"},
            }
        )

    def test_none_fields_omitted_from_insert(self):
        client, builder, response = _build_client_mock([{"id": "cp2"}])

        with patch("db.get_client", return_value=client):
            worker_state.write_checkpoint(
                "job-456",
                city=None,
                apn=None,
                row_offset=10,
                phase=None,
                extra=None,
            )

        payload = builder.insert.call_args[0][0]
        self.assertEqual(payload["job_id"], "job-456")
        self.assertEqual(payload["row_offset"], 10)
        self.assertNotIn("city", payload)
        self.assertNotIn("apn", payload)
        self.assertNotIn("phase", payload)
        self.assertNotIn("extra", payload)


class TestGetLatestCheckpoint(unittest.TestCase):
    def test_returns_checkpoint_when_found(self):
        checkpoint = {"id": "cp-1", "job_id": "job-1", "city": "BERKELEY"}
        client, builder, response = _build_client_mock([checkpoint])

        with patch("db.get_client", return_value=client):
            result = worker_state.get_latest_checkpoint("job-1")

        client.table.assert_called_once_with("scanner_job_checkpoints")
        builder.eq.assert_called_once_with("job_id", "job-1")
        builder.order.assert_called_once_with("created_at", desc=True)
        builder.limit.assert_called_once_with(1)
        self.assertEqual(result, checkpoint)

    def test_returns_none_when_no_results(self):
        client, builder, response = _build_client_mock([])

        with patch("db.get_client", return_value=client):
            result = worker_state.get_latest_checkpoint("job-no-checkpoints")

        self.assertIsNone(result)


class TestGetResumableJob(unittest.TestCase):
    def test_queries_interrupted_status_and_job_type(self):
        job_row = {"id": "job-resume-1", "job_type": "vpt_scan", "status": "interrupted"}
        checkpoint_row = {"id": "cp-resume-1", "job_id": "job-resume-1"}

        # We need two separate client mocks: one for the job query, one for the checkpoint.
        # Use a counter to return different data on successive get_client calls.
        job_client, job_builder, job_response = _build_client_mock([job_row])
        cp_client, cp_builder, cp_response = _build_client_mock([checkpoint_row])

        clients = [job_client, cp_client]
        call_count = 0

        def _get_client_side_effect():
            nonlocal call_count
            c = clients[call_count % len(clients)]
            call_count += 1
            return c

        with patch("db.get_client", side_effect=_get_client_side_effect):
            result = worker_state.get_resumable_job("vpt_scan")

        # Job query assertions
        job_client.table.assert_called_once_with("scanner_jobs")
        job_builder.eq.assert_any_call("status", "interrupted")
        job_builder.eq.assert_any_call("job_type", "vpt_scan")
        job_builder.order.assert_called_once_with("created_at", desc=True)
        job_builder.limit.assert_called_once_with(1)

        self.assertEqual(result["id"], "job-resume-1")
        self.assertEqual(result["checkpoint"], checkpoint_row)

    def test_returns_none_when_no_interrupted_jobs(self):
        client, builder, response = _build_client_mock([])

        with patch("db.get_client", return_value=client):
            result = worker_state.get_resumable_job("vpt_scan")

        self.assertIsNone(result)

    def test_includes_checkpoint_key(self):
        job_row = {"id": "j2", "job_type": "pge_scan", "status": "interrupted"}
        checkpoint_row = {"id": "cp-2", "job_id": "j2", "phase": "checking"}

        clients_data = [[job_row], [checkpoint_row]]
        call_count = 0

        def _get_client():
            nonlocal call_count
            c_mock, b_mock, r_mock = _build_client_mock(clients_data[call_count % len(clients_data)])
            call_count += 1
            return c_mock

        with patch("db.get_client", side_effect=_get_client):
            result = worker_state.get_resumable_job("pge_scan")

        self.assertIn("checkpoint", result)
        self.assertEqual(result["checkpoint"]["phase"], "checking")


class TestMarkDiscovery(unittest.TestCase):
    def test_update_called_with_null_filter_returns_true_on_update(self):
        client, builder, response = _build_client_mock([{"apn": "111-222-333"}])

        with patch("db.get_client", return_value=client):
            result = worker_state.mark_discovery("111-222-333", "job-disc-1")

        client.table.assert_called_once_with("bills")
        builder.update.assert_called_once_with(
            {"first_seen_at": "now()", "discovered_by_job_id": "job-disc-1"}
        )
        builder.eq.assert_called_once_with("apn", "111-222-333")
        builder.is_.assert_called_once_with("first_seen_at", "null")
        self.assertTrue(result)

    def test_returns_false_when_no_rows_updated(self):
        client, builder, response = _build_client_mock([])

        with patch("db.get_client", return_value=client):
            result = worker_state.mark_discovery("already-seen-apn", "job-disc-2")

        self.assertFalse(result)

    def test_returns_true_when_row_was_updated(self):
        client, builder, response = _build_client_mock([{"apn": "999-888-777"}])

        with patch("db.get_client", return_value=client):
            result = worker_state.mark_discovery("999-888-777", "job-new")

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
