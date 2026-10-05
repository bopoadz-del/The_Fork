"""Live Google Drive acceptance test, against the DEPLOYED build.

The deployment holds the Drive connection: an API-key caller resolves to the
``system`` user (``require_user``), whose OAuth token the deployed
``drive_auth`` stores and refreshes. So the live routes exercise exactly what
the old in-process version did with a locally connected token -- a real
``get_access_token("system")`` (refreshing at Google if expired) and a real
``GoogleDriveBlock`` list call -- which CI can never hold itself.

Gated by tests/_live_api.py: CI's production-like job runs it with the
FORK_API_KEY repository secret (skips without a key; fails under
LIVE_API_REQUIRED=1). Mocked contracts: test_drive_auth.py, test_drive_router.py.
"""
from __future__ import annotations

import pytest

from tests._live_api import call, require_live_api


@pytest.fixture(autouse=True)
def _live():
    require_live_api()


def test_live_drive_is_connected():
    status, body = call("GET", "/v1/drive/status", timeout=60)
    # The body names the connected account's email; report only the flags.
    flags = {k: body.get(k) for k in ("configured", "connected")}         if isinstance(body, dict) else body
    assert status == 200, (status, flags)
    assert flags == {"configured": True, "connected": True}, flags


def test_live_list_files():
    # 409 here means get_access_token found no token or Google refused the
    # refresh; 502 means the Drive list call itself failed.
    status, body = call("GET", "/v1/drive/files", timeout=60)
    assert status == 200, (status, body)
    assert isinstance(body.get("files"), list), sorted(body)
    assert body["folder_id"] == "root", body["folder_id"]
