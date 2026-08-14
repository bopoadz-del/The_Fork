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

from app import main as app_main


class _Req:
    """Minimal stand-in for starlette Request — only what the function reads."""

    def __init__(self, authz: str | None = None, host: str = "10.0.0.1"):
        self.headers = {"Authorization": authz} if authz else {}
        self.client = type("C", (), {"host": host})()


class _Recorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _identity(req, _unused=None):
    """Call the function with a handler attached DIRECTLY to its logger.

    Deliberately not pytest's caplog: caplog observes records via propagation
    to the root logger, so it is sensitive to whatever any earlier test in the
    session did to logging. This test passed alone and failed inside the full
    CI suite for exactly that reason. Attaching to ``app.main``'s own logger
    object measures what the function actually emits, independent of ambient
    configuration.
    """
    rec = _Recorder()
    lg = app_main.logger
    prev_level, prev_disabled = lg.level, logging.root.manager.disable
    logging.disable(logging.NOTSET)
    lg.setLevel(logging.WARNING)
    lg.addHandler(rec)
    try:
        ident = app_main._rate_limit_identity(req)
    finally:
        lg.removeHandler(rec)
        lg.setLevel(prev_level)
        logging.disable(prev_disabled)
    _identity.records = rec.records
    return ident


def test_api_key_caller_is_identified_without_logging():
    """The regression: a non-JWT bearer token must not produce a warning."""
    ident = _identity(_Req("Bearer cb_live_not_a_jwt_at_all"))
    assert ident.startswith("key:"), ident
    assert not _identity.records, (
        "an API key is not a JWT - that is the normal path and must be silent; "
        f"got {[r.getMessage() for r in _identity.records]}"
    )


def test_jwt_caller_is_identified_as_user_without_logging(monkeypatch):
    monkeypatch.setattr(app_main._jwt_auth, "decode_token",
                        lambda _t: {"user_id": "u-42"})
    ident = _identity(_Req("Bearer a.b.c"))
    assert ident == "user:u-42"
    assert not _identity.records


def test_unexpected_failure_is_still_loud(monkeypatch):
    """The other direction: quieting the normal case must not silence real bugs.

    A missing signing secret is not "this isn't a JWT" — it must still warn,
    or the fix would have traded noise for blindness.
    """
    def _boom(_t):
        raise RuntimeError("signing secret unavailable")

    monkeypatch.setattr(app_main._jwt_auth, "decode_token", _boom)
    ident = _identity(_Req("Bearer whatever"))
    assert ident.startswith("key:")
    assert any("_rate_limit_identity" in r.getMessage() for r in _identity.records), (
        "an unexpected exception must still be logged loudly"
    )


def test_anonymous_caller_falls_back_to_ip():
    assert _identity(_Req(None, host="203.0.113.9")) == "ip:203.0.113.9"
    assert not _identity.records


def test_same_key_yields_a_stable_identity():
    """Rate limiting is meaningless if the identity churns per request."""
    a = _identity(_Req("Bearer cb_live_same_key"))
    b = _identity(_Req("Bearer cb_live_same_key"))
    c = _identity(_Req("Bearer cb_live_other_key"))
    assert a == b and a != c
