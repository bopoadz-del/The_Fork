"""Rate-limit identification must be silent on the NORMAL path.

Live-fire campaign, run 1 (2026-08-14): production logs carried one full
traceback per request --

    WARNING app.main swallowed Exception in _rate_limit_identity() - continuing
    jwt.exceptions.DecodeError: Not enough segments

An API key is not a JWT, so ``decode_token`` raised for every key-authed
request. The result was correct (fall through to hashing the key) but it was
reached by exception, and the handler logged ``exc_info=True`` every time.
Real warnings were buried under per-request noise.

These tests fence the OBSERVABILITY property, not the return value: the normal
paths must emit no warning, while a genuinely unexpected failure must still be
loud. A test that only checked the returned identity would have stayed green
through the entire incident.
"""

import logging

import pytest

from app import main as app_main


class _Req:
    """Minimal stand-in for starlette Request — only what the function reads."""

    def __init__(self, authz: str | None = None, host: str = "10.0.0.1"):
        self.headers = {"Authorization": authz} if authz else {}
        self.client = type("C", (), {"host": host})()


def _identity(req, caplog):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.main"):
        return app_main._rate_limit_identity(req)  # noqa: SLF001


def test_api_key_caller_is_identified_without_logging(caplog):
    """The regression: a non-JWT bearer token must not produce a warning."""
    ident = _identity(_Req("Bearer cb_live_not_a_jwt_at_all"), caplog)
    assert ident.startswith("key:"), ident
    assert not caplog.records, (
        "an API key is not a JWT — that is the normal path and must be silent; "
        f"got {[r.getMessage() for r in caplog.records]}"
    )


def test_jwt_caller_is_identified_as_user_without_logging(monkeypatch, caplog):
    monkeypatch.setattr(app_main._jwt_auth, "decode_token",  # noqa: SLF001
                        lambda _t: {"user_id": "u-42"})
    ident = _identity(_Req("Bearer a.b.c"), caplog)
    assert ident == "user:u-42"
    assert not caplog.records


def test_unexpected_failure_is_still_loud(monkeypatch, caplog):
    """The other direction: quieting the normal case must not silence real bugs.

    A missing signing secret is not "this isn't a JWT" — it must still warn,
    or the fix would have traded noise for blindness.
    """
    def _boom(_t):
        raise RuntimeError("signing secret unavailable")

    monkeypatch.setattr(app_main._jwt_auth, "decode_token", _boom)  # noqa: SLF001
    ident = _identity(_Req("Bearer whatever"), caplog)
    assert ident.startswith("key:")
    assert any("_rate_limit_identity" in r.getMessage() for r in caplog.records), (
        "an unexpected exception must still be logged loudly"
    )


def test_anonymous_caller_falls_back_to_ip(caplog):
    assert _identity(_Req(None, host="203.0.113.9"), caplog) == "ip:203.0.113.9"
    assert not caplog.records


def test_same_key_yields_a_stable_identity(caplog):
    """Rate limiting is meaningless if the identity churns per request."""
    a = _identity(_Req("Bearer cb_live_same_key"), caplog)
    b = _identity(_Req("Bearer cb_live_same_key"), caplog)
    c = _identity(_Req("Bearer cb_live_other_key"), caplog)
    assert a == b and a != c
