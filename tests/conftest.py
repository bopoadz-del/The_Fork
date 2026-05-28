"""Pytest configuration and fixtures."""

import pytest
import os
import sys

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before test collection so env-gated tests (e.g. the live
# DEEPSEEK_API_KEY acceptance tests) see keys placed in .env. The app loads
# .env itself; conftest does it too so `skipif`s evaluated at collection time
# pick the key up without needing it exported in the shell.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

# Disable per-caller rate limiting for the test suite — the full suite makes
# far more than a minute's quota of requests under one identity (cb_dev_key).
# The rate limiter's own tests re-enable it explicitly via monkeypatch.
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"

# Isolate the suite's DATA_DIR so tests never write to the live ./data/ — the
# projects store, doc index, agent memory, and upload dir all derive their
# location from $DATA_DIR. Without this, every test that creates a project
# landed a row in the user's live projects.db (we found 2089 leaked rows
# from prior runs and 1806 synthetic `{hex}_name.pdf` fixture files). The
# override happens at collection time, BEFORE app modules import, so the
# resolved path on first read is the temp directory.
import tempfile as _tempfile
_TEST_DATA_DIR = _tempfile.mkdtemp(prefix="thefork-tests-")
# Force the override — start-local.sh sets DATA_DIR=$PWD/data so a developer
# running pytest in the same shell after the server would otherwise inherit
# the live data dir.
_existing = os.environ.get("DATA_DIR", "")
if not _existing or os.path.abspath(_existing) == os.path.abspath("./data"):
    os.environ["DATA_DIR"] = _TEST_DATA_DIR

@pytest.fixture
def sample_text():
    return "Hello, this is a test document for Cerebrum Blocks."

@pytest.fixture
def sample_code():
    return """
def hello_world():
    print("Hello, World!")
    return 42

class MyClass:
    def __init__(self):
        self.value = 10
"""

@pytest.fixture
def data_dir():
    return "/app/data"
