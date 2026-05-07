"""Browser-test fixtures.

Spawns a uvicorn instance pinned to a random port for the test session and
gives every test a Playwright `page` already pointed at it. Captures console
logs and network requests automatically — tests can assert on them.

Pre-requisites (run once):
    pip install pytest-playwright playwright
    python -m playwright install chromium

Run:
    pytest tests/browser/ -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Iterator, List

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 25.0) -> None:
    start = time.time()
    last_err: Exception | None = None
    while time.time() - start < timeout:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.4)
    raise RuntimeError(f"server at {url} did not come up within {timeout}s (last={last_err})")


@pytest.fixture(scope="session")
def app_server() -> Iterator[str]:
    """Boot uvicorn once for the test session, return base URL."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["ENV"] = "development"
    env["DATA_DIR"] = str(REPO_ROOT / "data")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(f"{base_url}/v1/health")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def browser_console(page) -> List[dict]:
    """List of console messages observed during the test."""
    msgs: List[dict] = []
    page.on("console", lambda m: msgs.append({"type": m.type, "text": m.text, "location": m.location}))
    page.on("pageerror", lambda e: msgs.append({"type": "pageerror", "text": str(e), "location": {}}))
    return msgs


@pytest.fixture
def browser_network(page) -> List[dict]:
    """List of network responses observed during the test."""
    requests: List[dict] = []
    page.on("response", lambda r: requests.append({
        "url": r.url,
        "status": r.status,
        "method": r.request.method,
    }))
    return requests


@pytest.fixture
def app_page(page, app_server, browser_console, browser_network):
    """Playwright page already pointed at the running app, with console+network fixtures wired."""
    page.goto(f"{app_server}/", wait_until="networkidle")
    return page
