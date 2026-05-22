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
