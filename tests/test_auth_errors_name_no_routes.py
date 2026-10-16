"""A user-facing auth error never names an API route.

An error a person reads says what to do in the product ("Resend confirmation
email" on the sign-in page), never which endpoint to call. Checked through
real requests, and over every HTTPException text in the auth router.
"""
import ast
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

AUTH_ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "users.py"


class _Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"id": "msg_test"}


def _configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "The Shovel <noreply@theshovel.ai>")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://theshovel.ai")


def _detail(resp) -> str:
    body = resp.json()
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return detail if isinstance(detail, str) else str(detail)


def test_an_unverified_login_points_at_the_sign_in_page_not_a_route(monkeypatch):
    _configured(monkeypatch)
    addr = f"routes-{uuid.uuid4().hex[:10]}@example.com"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_Resp()), TestClient(app) as c:
        c.post("/v1/users/register", json={"email": addr, "password": "password12"})
        blocked = c.post("/v1/users/login", json={"email": addr, "password": "password12"})
    assert blocked.status_code == 403, blocked.text
    text = _detail(blocked)
    assert "/v1/" not in text
    assert "Resend confirmation email" in text


def test_registering_an_existing_address_answers_exactly_like_a_new_one(monkeypatch):
    """LANE4 #978: a registration must not reveal that an account exists."""
    _configured(monkeypatch)
    existing = f"routes-{uuid.uuid4().hex[:10]}@example.com"
    fresh = f"routes-{uuid.uuid4().hex[:10]}@example.com"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_Resp()), TestClient(app) as c:
        c.post("/v1/users/register", json={"email": existing, "password": "password12"})
        again = c.post("/v1/users/register", json={"email": existing, "password": "other-pass-34"})
        new = c.post("/v1/users/register", json={"email": fresh, "password": "password12"})
    assert again.status_code == new.status_code == 201
    assert {**again.json(), "email": "x"} == {**new.json(), "email": "x"}
    assert "already" not in again.text.lower() and existing.split("@")[0] in again.text  # echoes only what was typed


def test_other_auth_failures_name_no_route_either(monkeypatch):
    _configured(monkeypatch)
    addr = f"routes-{uuid.uuid4().hex[:10]}@example.com"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_Resp()), TestClient(app) as c:
        c.post("/v1/users/register", json={"email": addr, "password": "password12"})
        responses = [
            c.post("/v1/users/login", json={"email": addr, "password": "wrong-password"}),
            c.post("/v1/users/login", json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com",
                                             "password": "password12"}),
        ]
    for resp in responses:
        assert resp.status_code >= 400, resp.text
        assert "/v1/" not in _detail(resp), resp.text


def test_no_auth_error_text_in_the_router_names_a_route():
    tree = ast.parse(AUTH_ROUTER.read_text(encoding="utf-8"))
    leaks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException":
            for arg in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "/v1/" in sub.value:
                        leaks.append((node.lineno, sub.value))
    assert not leaks, leaks
