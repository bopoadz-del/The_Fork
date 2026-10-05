"""Client for the tests that check the LIVE deployed build.

These tests call the deployed API with ``FORK_API_KEY`` (a repository
secret; ``FORK_BASE_URL`` optionally overrides the host). CI runs them in the
production-like job's "Live-deploy tests" step, which sets
``LIVE_API_REQUIRED=1`` whenever the secret is available to the run (any
same-repo PR or push; never a fork PR, where GitHub withholds secrets).
Under that flag a missing key FAILS instead of skipping, so the tests cannot
silently stop running.

Run locally after a deploy:

    FORK_API_KEY=... python -m pytest tests/test_*_live.py -q

The key is sent only as the Authorization header and never printed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

DEFAULT_BASE = "https://theshovel.ai"


def base_url() -> str:
    # `or`, not a getenv default: CI passes an unset secret as "".
    return (os.getenv("FORK_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _key() -> str:
    return (os.getenv("FORK_API_KEY") or "").strip()


def require_live_api() -> None:
    """Skip (or, under LIVE_API_REQUIRED=1, fail) when no key is configured."""
    if _key():
        return
    if os.getenv("LIVE_API_REQUIRED", "").strip() == "1":
        pytest.fail("LIVE_API_REQUIRED=1 but FORK_API_KEY is not set")
    pytest.skip("FORK_API_KEY not set -- live deploy test")


def _request(method: str, path: str, payload: Any = None) -> urllib.request.Request:
    data = None if payload is None else json.dumps(payload).encode()
    return urllib.request.Request(
        f"{base_url()}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {_key()}",
                 "Content-Type": "application/json"},
    )


def call(method: str, path: str, payload: Any = None,
         timeout: float = 180) -> tuple[int, Any]:
    """Return (HTTP status, JSON body). Non-2xx is returned, not raised."""
    req = _request(method, path, payload)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        body = json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        body = {"_raw": raw[:500].decode("utf-8", "replace")}
    return status, body


def stream_chat(message: str, project_id: str, timeout: float = 180) -> str:
    """POST /v1/chat/stream and return the concatenated token text."""
    req = _request("POST", "/v1/chat/stream",
                   {"message": message, "project_id": project_id})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    text = ""
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            ev = None  # keep-alive / non-JSON frame
        if isinstance(ev, dict) and ev.get("type") == "token":
            text += ev.get("content", "")
    return " ".join(text.split())
