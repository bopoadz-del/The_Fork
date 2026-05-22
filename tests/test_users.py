"""Tests for the users store — Stream A (User Accounts & Multi-Tenancy)."""
import importlib
import pytest
from app.core import users as users_mod


@pytest.fixture
def users(monkeypatch, tmp_path):
    """Relocate the users DB into a tmp dir and reload the module."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return importlib.reload(users_mod)


def test_init_creates_system_user(users):
    users.init_db()
    sys_user = users.get_user_by_id("system")
    assert sys_user is not None
    assert sys_user["id"] == "system"
    assert sys_user["role"] == "admin"
    assert sys_user["email"] == "system@local"


def test_init_db_is_idempotent(users):
    users.init_db()
    users.init_db()
    assert users.get_user_by_id("system")["id"] == "system"


def test_create_user_and_password_round_trip(users):
    users.init_db()
    u = users.create_user("alice@example.com", "s3cret-pw", display_name="Alice")
    assert u["email"] == "alice@example.com"
    assert u["role"] == "user"
    assert u["id"] != "system"
    assert "password_hash" not in u
    stored = users.get_user_by_email("alice@example.com")
    assert users.verify_password("s3cret-pw", stored["password_hash"], stored["salt"]) is True
    assert users.verify_password("wrong-pw", stored["password_hash"], stored["salt"]) is False


def test_create_user_rejects_duplicate_email(users):
    users.init_db()
    users.create_user("dup@example.com", "pw1")
    with pytest.raises(ValueError):
        users.create_user("DUP@example.com", "pw2")  # case-insensitive


def test_create_user_normalizes_email_lowercase(users):
    users.init_db()
    u = users.create_user("MixedCase@Example.com", "pw")
    assert u["email"] == "mixedcase@example.com"
