import time
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core import drive_auth

H = {"Authorization": "Bearer cb_dev_key"}


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/v1/drive/callback")
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_connect_returns_auth_url(client):
    # /connect returns the consent URL as JSON (NOT a redirect) so the browser
    # can fetch it with the Bearer header, then navigate client-side.
    r = client.get("/v1/drive/connect", headers=H)
    assert r.status_code == 200
    url = r.json()["auth_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "scope=" in url and "drive.readonly" in url
    assert "state=" in url and "access_type=offline" in url


def test_connect_requires_auth(client):
    assert client.get("/v1/drive/connect").status_code == 401


def test_callback_rejects_bad_state(client):
    r = client.get("/v1/drive/callback?code=x&state=never-issued",
                    follow_redirects=False)
    assert r.status_code == 400


def test_callback_exchanges_code_and_stores_token(client, monkeypatch):
    # issue a state via /connect
    r = client.get("/v1/drive/connect", headers=H)
    state = r.json()["auth_url"].split("state=")[1].split("&")[0]

    async def fake_exchange(code):
        assert code == "auth-code"
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    async def fake_email(access_token):
        return "me@example.com"

    import app.routers.drive as drive_mod
    monkeypatch.setattr(drive_mod, "_exchange_code", fake_exchange)
    monkeypatch.setattr(drive_mod, "_fetch_email", fake_email)

    r = client.get(f"/v1/drive/callback?code=auth-code&state={state}",
                    follow_redirects=False)
    assert r.status_code in (302, 307)
    tok = drive_auth.load_token()
    assert tok["access_token"] == "AT" and tok["refresh_token"] == "RT"
    assert tok["email"] == "me@example.com"
    assert tok["expiry"] > time.time()


def test_status_not_connected(client):
    r = client.get("/v1/drive/status", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False and body["configured"] is True


def test_status_connected_after_token(client):
    drive_auth.save_token({"access_token": "AT", "refresh_token": "RT",
                           "expiry": time.time() + 9999, "email": "me@x.com"})
    body = client.get("/v1/drive/status", headers=H).json()
    assert body["connected"] is True and body["email"] == "me@x.com"


def test_disconnect_clears_token(client):
    drive_auth.save_token({"access_token": "AT", "expiry": time.time() + 9999})
    assert client.post("/v1/drive/disconnect", headers=H).status_code == 200
    assert drive_auth.load_token() is None


def test_files_requires_connection(client):
    r = client.get("/v1/drive/files", headers=H)
    assert r.status_code == 409  # not connected


def test_files_lists_when_connected(client, monkeypatch):
    drive_auth.save_token({"access_token": "AT", "refresh_token": "RT",
                           "expiry": time.time() + 9999})

    async def fake_token():
        return "AT"

    async def fake_process(self, input_data, params=None):
        assert params.get("operation") == "list"
        assert params.get("access_token") == "AT"
        return {"status": "success", "files": [
            {"id": "f1", "name": "Plan.pdf", "type": "pdf"}]}

    monkeypatch.setattr(drive_auth, "get_access_token", fake_token)
    from app.blocks.google_drive import GoogleDriveBlock
    monkeypatch.setattr(GoogleDriveBlock, "process", fake_process)

    r = client.get("/v1/drive/files?q=plan", headers=H)
    assert r.status_code == 200
    assert r.json()["files"][0]["name"] == "Plan.pdf"


def test_files_requires_auth(client):
    assert client.get("/v1/drive/files").status_code == 401
