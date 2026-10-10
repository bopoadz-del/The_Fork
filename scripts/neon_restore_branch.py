#!/usr/bin/env python3
"""Create a Neon branch as a restore point. Prints the branch id only.

Looks for NEON_API_KEY or NEON_API_TOKEN, and optional NEON_PROJECT_ID.
Fails clearly when neither API secret is present. Never prints connection
strings, rule text, or row text.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

_PREFIX = "SCRUB_AT_SOURCE"
_API = "https://console.neon.tech/api/v2"
_SECRET_NAMES = ("NEON_API_KEY", "NEON_API_TOKEN")


def _emit(**fields: object) -> None:
    parts = [f"{key}={fields[key]}" for key in fields]
    print(f"{_PREFIX} {' '.join(parts)}", flush=True)


def missing_secret_message() -> str:
    looked = ",".join(_SECRET_NAMES)
    return f"{_PREFIX} error=neon_secret_missing looked={looked}"


def api_key_from_env(env: Optional[Dict[str, str]] = None) -> str:
    src = env if env is not None else os.environ
    for name in _SECRET_NAMES:
        value = (src.get(name) or "").strip()
        if value:
            return value
    return ""


def _request(
    method: str,
    path: str,
    key: str,
    body: Optional[dict] = None,
    *,
    opener: Optional[Callable[..., Any]] = None,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"neon_http_{exc.code}") from exc
    return json.loads(raw or "{}")


def resolve_project_id(
    key: str, *, opener: Optional[Callable[..., Any]] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    src = env if env is not None else os.environ
    given = (src.get("NEON_PROJECT_ID") or "").strip()
    if given:
        return given
    payload = _request("GET", "/projects", key, opener=opener)
    items: List[dict] = payload.get("projects") or []
    if len(items) == 1:
        return str(items[0].get("id") or "")
    raise RuntimeError(f"neon_project_unresolved count={len(items)}")


def create_restore_branch(
    name: str,
    *,
    key: str,
    project_id: str,
    opener: Optional[Callable[..., Any]] = None,
) -> str:
    payload = _request(
        "POST",
        f"/projects/{project_id}/branches",
        key,
        {"branch": {"name": name}},
        opener=opener,
    )
    branch = payload.get("branch") or payload
    branch_id = str(branch.get("id") or "")
    if not branch_id:
        raise RuntimeError("neon_branch_id_missing")
    return branch_id


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Neon restore-point branch.")
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    key = api_key_from_env()
    if not key:
        print(missing_secret_message(), flush=True)
        return 2
    try:
        project_id = resolve_project_id(key)
        if not project_id:
            _emit(error="neon_project_id_empty")
            return 2
        branch_id = create_restore_branch(args.name, key=key, project_id=project_id)
    except RuntimeError as exc:
        _emit(error=str(exc))
        return 2
    _emit(restore_branch=branch_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
