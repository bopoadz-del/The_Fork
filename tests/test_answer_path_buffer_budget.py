"""One question has a bounded database budget.

Live, before the bounded-read fix: one chat question read ~667 MB from storage
and touched ~2.7 GB of buffers (the chunk table is ~1.4 GB with its TOASTed
embeddings). After: ~26 MB read, ~460 MB touched. This pins it so the next
tool, chunker or retrieval change cannot walk it back quietly.

A full chat turn runs against a seeded synthetic corpus (the plan test's
seeding) with a scripted model, every statement the turn sends is recorded,
and each read is re-run under EXPLAIN (ANALYZE, BUFFERS). Budgets:

* buffers read from storage per question <= 50 MB;
* buffers touched per question <= 600 MB;
* and, scale-free, no single statement on the chunk table touches more than
  a quarter of that table's own size -- the synthetic corpus is smaller than
  live, so the absolute numbers alone could pass while a full-table read crept
  back. (A turn's TOTAL counts the same index pages once per statement, so the
  bound is per statement.) The control proves it bites: one full read of the
  table's text and embeddings exceeds it.

PostgreSQL only (buffer counts are a PostgreSQL executor property): the CI
job ``test-postgres`` runs it.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from sqlalchemy import event, text

from tests.test_answer_path_query_plans import GK, CORPUS, OWN, _seed

pytestmark = pytest.mark.skipif(
    os.getenv("PYTEST_USE_POSTGRES", "").strip().lower() not in ("1", "true", "yes"),
    reason="buffer counts are a PostgreSQL executor property (test-postgres job)",
)

PAGE = 8192
READ_BUDGET = 50 * 1024 * 1024
TOUCH_BUDGET = 600 * 1024 * 1024
TABLE_SHARE = 0.25

#: An item-code lookup on the project (the shape of the live measurement).
QUESTION = "What is the quantity and rate for bill item Q731.4?"


def _buffers(engine, statement: str, params) -> tuple[int, int]:
    """(shared read, shared hit) blocks of one statement, re-run read-only."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
        plan = cur.fetchone()[0]
        if isinstance(plan, str):
            plan = json.loads(plan)
        top = plan[0]["Plan"]
        return int(top.get("Shared Read Blocks", 0)), int(top.get("Shared Hit Blocks", 0))
    finally:
        raw.rollback()
        raw.close()


def _reads(statements):
    """Plain reads (row locks cannot be re-run read-only and are not reads)."""
    return [(s, p) for s, p in statements
            if s.lstrip().split(None, 1)[0].upper() in ("SELECT", "WITH")
            and "FOR UPDATE" not in s.upper()]


def test_one_question_stays_inside_its_database_budget(monkeypatch):
    from app.agents import runtime as rt
    from app.core.db import get_engine
    from app.core.rag import vector_store
    from app.core.rag.embeddings import get_embedder

    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", GK)
    monkeypatch.setenv("MASTER_CORPUS_SOURCE_PROJECT_ID", CORPUS)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")

    engine = get_engine()
    store = vector_store.get_store(dim=get_embedder().dim)
    table = store._table_name
    with engine.connect() as conn:
        seeded = conn.execute(text("SELECT count(*) FROM documents WHERE id = 'qpdoc0'")).scalar()
    if not seeded:  # the plan test may already have seeded this database
        _seed(engine, table)

    rt.load_agents()
    agent = rt.AGENT_REGISTRY["project-assistant"]

    async def scripted(self, *args, **kw):
        return {"status": "success", "raw": {"model": "scripted"},
                "choice": {"message": {"content": "The item is not in the excerpts.", "tool_calls": []}}}

    monkeypatch.setattr(rt.Agent, "_call_llm", scripted)
    monkeypatch.setattr(rt, "project_is_rag_ready", lambda pid: True)

    # Warm the worker the way a live one is warm.
    asyncio.run(agent.chat("warm up the drainage works", project_id=OWN, user_id="u-budget"))

    statements: list = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        asyncio.run(agent.chat(QUESTION, project_id=OWN, user_id="u-budget"))
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    reads = _reads(statements)
    assert any(table in s for s, _ in reads), "the turn never read the chunk table"
    read_blocks = hit_blocks = 0
    worst_on_table = 0
    for statement, params in reads:
        r, h = _buffers(engine, statement, params)
        read_blocks += r
        hit_blocks += h
        if table in statement:
            worst_on_table = max(worst_on_table, (r + h) * PAGE)
    read_bytes = read_blocks * PAGE
    touched = (read_blocks + hit_blocks) * PAGE
    with engine.connect() as conn:
        table_bytes = int(conn.execute(text(f"SELECT pg_total_relation_size('{table}')")).scalar())

    assert read_bytes <= READ_BUDGET, f"read {read_bytes / 1e6:.1f} MB from storage for one question"
    assert touched <= TOUCH_BUDGET, f"touched {touched / 1e6:.1f} MB of buffers for one question"
    assert worst_on_table <= TABLE_SHARE * table_bytes, (
        f"one statement touched {worst_on_table / 1e6:.1f} MB of a {table_bytes / 1e6:.1f} MB "
        f"chunk table (> {TABLE_SHARE:.0%})"
    )

    # Control: one full read of the table's text and embeddings -- what the
    # unbounded path effectively did -- breaks the scale-free bound, so the
    # bound is not vacuous at this corpus size.
    r, h = _buffers(engine, f"SELECT sum(length(text)), sum(vector_dims(embedding)) FROM {table}", None)
    assert (r + h) * PAGE > TABLE_SHARE * table_bytes, "control did not exceed the bound"

