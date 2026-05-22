"""End-to-end tests for register/login/me — Stream A."""
import os
import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def tmp_data_dir(tmp_path_factory):
    """Module-scoped temporary DATA_DIR so all tests share one DB."""
    return str(tmp_path_factory.mktemp("user_api_data"))


@pytest.fixture(scope="module", autouse=True)
def isolate_data_dir(tmp_data_dir):
    """Set DATA_DIR and reset module state before the module-scoped client."""
    os.environ["DATA_DIR"] = tmp_data_dir
    # Reset module state so both modules use the tmp path
    import app.core.users as u_mod
    import app.core.jwt_auth as jwt_mod
    u_mod._initialized = False
    jwt_mod._cached_secret = None
    yield
    # Teardown: reset state
    u_mod._initialized = False
    jwt_mod._cached_secret = None
    os.environ.pop("DATA_DIR", None)


@pytest.fixture(scope="module")
def client(tmp_data_dir):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _register(client, email, pw="pw-123456", name="Test"):
    return client.post("/v1/users/register",
                        json={"email": email, "password": pw, "display_name": name})


def test_register_returns_user_no_secrets(client):
    r = _register(client, "reg1@example.com")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "reg1@example.com"
    assert "password_hash" not in body and "salt" not in body
    assert body["role"] == "user"


def test_register_duplicate_email_409(client):
    _register(client, "dup-api@example.com")
    r = _register(client, "dup-api@example.com")
    assert r.status_code == 409


def test_login_returns_jwt(client):
    _register(client, "login1@example.com", pw="goodpassword")
    r = client.post("/v1/users/login",
                     json={"email": "login1@example.com", "password": "goodpassword"})
    assert r.status_code == 200, r.text
    assert "token" in r.json() and r.json()["token"]


def test_login_wrong_password_401(client):
    _register(client, "login2@example.com", pw="rightpw99")
    r = client.post("/v1/users/login",
                     json={"email": "login2@example.com", "password": "wrongpw"})
    assert r.status_code == 401


def test_me_with_jwt(client):
    _register(client, "me1@example.com", pw="mypassword1")
    token = client.post("/v1/users/login",
                         json={"email": "me1@example.com",
                               "password": "mypassword1"}).json()["token"]
    r = client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me1@example.com"


def test_me_with_legacy_api_key_is_system_user(client):
    r = client.get("/v1/users/me", headers={"Authorization": "Bearer cb_dev_key"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "system"
