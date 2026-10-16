"""Test helper: a Flask test client that is already signed in to the scanner web UI."""
from __future__ import annotations

import time
from types import ModuleType


def signed_in_client(webgui_app: ModuleType, role: str = "scout", user_id: str = "user-1"):
    client = webgui_app.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["user_email"] = f"{role}@example.com"
        sess["role"] = role
        sess["role_checked_at"] = time.time()
    return client
