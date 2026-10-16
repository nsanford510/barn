from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCAN_DIR = Path(__file__).resolve().parents[1]
if str(SCAN_DIR) not in sys.path:
    sys.path.insert(0, str(SCAN_DIR))

import research_queue
from webgui_auth_helpers import signed_in_client


def _load_webgui_app() -> ModuleType:
    module_name = "test_webgui_app"
    module_path = SCAN_DIR / "webgui" / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VacancyResearchStatusEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.webgui_app = _load_webgui_app()

    def test_returns_the_queue_status(self) -> None:
        status = research_queue.summarize({"pending": 4, "done": 1}, {"unknown": 1}, [], [])
        with patch.object(research_queue, "status", return_value=status):
            response = signed_in_client(self.webgui_app).get("/api/vacancy-research/status")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(4, payload["tasks"]["pending"])
        self.assertEqual(20.0, payload["pct"])
        self.assertEqual([], payload["agents"])

    def test_reports_an_error_when_the_queue_is_unavailable(self) -> None:
        with patch.object(research_queue, "status", side_effect=RuntimeError("relation does not exist")):
            response = signed_in_client(self.webgui_app).get("/api/vacancy-research/status")

        self.assertEqual(500, response.status_code)
        self.assertIn("relation does not exist", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
