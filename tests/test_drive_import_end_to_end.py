"""Google Drive, end to end, against a stubbed Google.

Disconnect used to delete only our copy of the token. The grant stayed live at
Google: the app kept showing as connected in the user's Google account, and
the refresh token kept working for anyone holding a copy of it.
"""
import asyncio
import time

import httpx
import pytest

from app.core import drive_auth


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _stub_revoke(monkeypatch, outcome=True):
    seen = []

    async def fake(token):
        seen.append(token)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(drive_auth, "_revoke_request", fake)
    return seen


def test_disconnect_revokes_the_whole_grant_at_google(monkeypatch):
    seen = _stub_revoke(monkeypatch)
    drive_auth.save_token("u1", {"access_token": "AT", "refresh_token": "RT",
                                 "expiry": time.time() + 999})
    out = asyncio.run(drive_auth.revoke_and_clear("u1"))
    # The refresh token, not the access token: revoking it ends the grant.
    assert seen == ["RT"]
    assert out == {"was_connected": True, "revoked": True}
    assert drive_auth.load_token("u1") is None


@pytest.mark.parametrize("outcome", [False, httpx.ConnectError("down")])
def test_a_failed_revoke_still_disconnects_and_says_so(monkeypatch, outcome):
    _stub_revoke(monkeypatch, outcome)
    drive_auth.save_token("u1", {"access_token": "AT", "refresh_token": "RT",
                                 "expiry": time.time() + 999})
    out = asyncio.run(drive_auth.revoke_and_clear("u1"))
    assert out == {"was_connected": True, "revoked": False}
    assert drive_auth.load_token("u1") is None


def test_disconnecting_when_not_connected_calls_nobody(monkeypatch):
    seen = _stub_revoke(monkeypatch)
    out = asyncio.run(drive_auth.revoke_and_clear("u1"))
    assert seen == []
    assert out == {"was_connected": False, "revoked": False}


def test_one_users_disconnect_leaves_another_users_grant_alone(monkeypatch):
    seen = _stub_revoke(monkeypatch)
    drive_auth.save_token("u1", {"access_token": "A1", "refresh_token": "R1",
                                 "expiry": time.time() + 999})
    drive_auth.save_token("u2", {"access_token": "A2", "refresh_token": "R2",
                                 "expiry": time.time() + 999})
    asyncio.run(drive_auth.revoke_and_clear("u1"))
    assert seen == ["R1"]
    assert drive_auth.load_token("u2")["refresh_token"] == "R2"
