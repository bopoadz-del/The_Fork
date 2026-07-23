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

# Default suite uses isolated SQLite (the_fork.db under temp DATA_DIR).
# CI job test-postgres sets PYTEST_USE_POSTGRES=1 and DATABASE_URL explicitly.
if os.getenv("PYTEST_USE_POSTGRES", "").strip().lower() not in ("1", "true", "yes"):
    os.environ.pop("DATABASE_URL", None)
else:
    # Fake embedder is 256-dim, aligned with pgvector schema (model2vec default).
    os.environ.setdefault("RAG_EMBEDDING_MODEL", "fake")

def is_extended_boot() -> bool:
    """Legacy platform boot — extended blocks (drives, MCP, etc.) are loaded."""
    return os.getenv("CEREBRUM_VIRGIN", "true").strip().lower() in ("0", "false", "no")


def is_construction_kit_enabled() -> bool:
    from app.core.domain_kit_loader import active_kit_ids

    return "construction" in active_kit_ids()


def listable_block_count() -> int:
    """Non-container blocks exposed by GET /blocks (matches blocks router)."""
    from app.blocks import get_all_blocks
    from app.core.universal_base import UniversalContainer

    return sum(
        1
        for cls in get_all_blocks().values()
        if not issubclass(cls, UniversalContainer)
    )


_CONSTRUCTION_KIT_SKIP = pytest.mark.skipif(
    not is_construction_kit_enabled(),
    reason="requires CEREBRUM_DOMAIN_KITS=construction",
)
_EXTENDED_BOOT_SKIP = pytest.mark.skipif(
    not is_extended_boot(),
    reason="requires CEREBRUM_VIRGIN=false",
)

# Module-level: pytestmark = construction_kit_markers
construction_kit_markers = [pytest.mark.construction_kit, _CONSTRUCTION_KIT_SKIP]
extended_boot_markers = [pytest.mark.extended_boot, _EXTENDED_BOOT_SKIP]


def requires_construction_kit(func):
    func = pytest.mark.construction_kit(func)
    return _CONSTRUCTION_KIT_SKIP(func)


def requires_extended_boot(func):
    func = pytest.mark.extended_boot(func)
    return _EXTENDED_BOOT_SKIP(func)


def _postgres_test_mode() -> bool:
    return os.getenv("PYTEST_USE_POSTGRES", "").strip().lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def _isolate_postgres_db():
    """Truncate unified schema between tests when running against PostgreSQL CI."""
    if not _postgres_test_mode():
        yield
        return

    from sqlalchemy import text

    from app.core.db import get_engine

    tables = (
        "chunks",
        "rag_budget",
        "hydration_runs",
        "runs",
        "doc_index",
        "agent_facts",
        "messages",
        "conversations",
        "workflows",
        "project_facts",
        "documents",
        "projects",
        "users",
    )
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(tables)
                + " RESTART IDENTITY CASCADE"
            )
        )

    from app.core import users as users_store

    users_store._initialized = False  # noqa: SLF001
    users_store.init_db()

    from app.core.rag import embeddings as _emb, vector_store as _vs

    _emb.reset_embedder_cache()
    _vs.reset_store_cache()

    yield


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


# NOTE: the previous autouse event-loop cleanup fixture was removed after the
# legacy Playwright browser suite was deleted. pytest-asyncio now manages loop
# lifecycle; forcibly closing the loop after every test broke module-scoped
# async fixtures (RuntimeError: Event loop is closed).
