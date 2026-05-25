"""Per-caller rate limiting — covers every request, including JWT sessions."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import rate_limit


def test_check_and_record_allows_then_blocks(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    rate_limit._buckets.clear()

    ident = "unit-test-id"
    assert all(rate_limit.check_and_record(ident) for _ in range(5))
    # 6th request in the window is over the limit.
    assert rate_limit.check_and_record(ident) is False


def test_disabled_when_limit_is_zero(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    rate_limit._buckets.clear()
    assert all(rate_limit.check_and_record("anything") for _ in range(100))


def test_identities_have_independent_budgets(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    rate_limit._buckets.clear()

    assert rate_limit.check_and_record("caller-A")
    assert rate_limit.check_and_record("caller-A")
    assert rate_limit.check_and_record("caller-A") is False  # A exhausted
    # A different caller still has its full budget.
    assert rate_limit.check_and_record("caller-B")


def test_middleware_returns_429_over_the_limit(monkeypatch):
    """The HTTP middleware throttles a caller regardless of auth type."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    rate_limit._buckets.clear()

    with TestClient(app) as c:
        headers = {"Authorization": "Bearer rate-limit-probe-token"}
        codes = [c.get("/v1/projects", headers=headers).status_code
                 for _ in range(5)]

    # The first 3 pass the limiter (whatever the endpoint itself returns);
    # the 4th and 5th are rejected with 429.
    assert codes[0] != 429
    assert codes[3] == 429 and codes[4] == 429, codes
