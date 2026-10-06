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
  half of that table's data (heap + TOAST) -- the synthetic corpus is smaller
  than live, so the absolute numbers alone could pass while a full-table read
  crept back. (A turn's TOTAL counts the same index pages once per statement,
  so the bound is per statement.) The control proves it bites: one full read
  of the table's text and embeddings exceeds it.

The arithmetic is a pure function, tested without a database; the turn itself
needs PostgreSQL buffer counts and runs in the ``test-postgres`` CI job.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Iterable

import pytest

PAGE = 8192
READ_BUDGET = 50 * 1024 * 1024
TOUCH_BUDGET = 600 * 1024 * 1024
TABLE_SHARE = 0.5

#: An item-code lookup on the project (the shape of the live measurement).
QUESTION = "What is the quantity and rate for bill item Q731.4?"

_ON_POSTGRES = os.getenv("PYTEST_USE_POSTGRES", "").strip().lower() in ("1", "true", "yes")


def plain_reads(statements: Iterable) -> list:
    """Plain reads (row locks cannot be re-run read-only and are not reads)."""
    return [(s, p) for s, p in statements
            if s.lstrip().split(None, 1)[0].upper() in ("SELECT", "WITH")
            and "FOR UPDATE" not in s.upper()]


def budget_report(reads: list, measure: Callable, table: str) -> dict:
    """Sum (read, hit) blocks over the turn's reads; find the worst single
    statement on the chunk table."""
    read_blocks = hit_blocks = 0
    worst_bytes, worst_stmt = 0, ""
    for statement, params in reads:
        r, h = measure(statement, params)
        read_blocks += r
        hit_blocks += h
        if table in statement and (r + h) * PAGE > worst_bytes:
            worst_bytes, worst_stmt = (r + h) * PAGE, " ".join(statement.split())[:300]
    return {"read_bytes": read_blocks * PAGE, "touched": (read_blocks + hit_blocks) * PAGE,
            "worst_bytes": worst_bytes, "worst_statement": worst_stmt}


def check_budget(report: dict, table_data_bytes: int) -> list:
    """Every budget the report breaks, as messages (empty = within budget)."""
    out = []
    if report["read_bytes"] > READ_BUDGET:
        out.append(f"read {report['read_bytes'] / 1e6:.1f} MB from storage for one question")
    if report["touched"] > TOUCH_BUDGET:
        out.append(f"touched {report['touched'] / 1e6:.1f} MB of buffers for one question")
    if report["worst_bytes"] > TABLE_SHARE * table_data_bytes:
        out.append(f"one statement touched {report['worst_bytes'] / 1e6:.1f} MB of "
                   f"{table_data_bytes / 1e6:.1f} MB chunk-table data (> {TABLE_SHARE:.0%}): "
                   f"{report['worst_statement']}")
    return out


# ── the arithmetic, without a database ───────────────────────────────────

def test_plain_reads_keep_selects_and_drop_writes_and_locks():
    stmts = [("SELECT 1", None), ("WITH x AS (SELECT 1) SELECT * FROM x", None),
             ("INSERT INTO t VALUES (1)", None), ("SELECT * FROM t FOR UPDATE", None)]
    assert [s for s, _ in plain_reads(stmts)] == ["SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x"]


def test_budget_report_sums_and_names_the_worst_chunk_statement():
    blocks = {"SELECT a FROM chunks_t": (10, 90), "SELECT b FROM documents": (1, 9),
              "SELECT c FROM chunks_t WHERE x": (0, 500)}
    rep = budget_report([(s, None) for s in blocks], lambda s, p: blocks[s], "chunks_t")
    assert rep["read_bytes"] == 11 * PAGE and rep["touched"] == 610 * PAGE
    assert rep["worst_bytes"] == 500 * PAGE and rep["worst_statement"] == "SELECT c FROM chunks_t WHERE x"


def test_check_budget_flags_each_broken_limit():
    ok = {"read_bytes": 1, "touched": 1, "worst_bytes": 1, "worst_statement": ""}
    assert check_budget(ok, 1000) == []
    bad = {"read_bytes": READ_BUDGET + 1, "touched": TOUCH_BUDGET + 1,
           "worst_bytes": 600, "worst_statement": "SELECT * FROM chunks_t"}
    msgs = check_budget(bad, 1000)
    assert len(msgs) == 3 and "SELECT * FROM chunks_t" in msgs[2]


# ── the turn, on PostgreSQL ───────────────────────────────────────────────

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


@pytest.mark.skipif(not _ON_POSTGRES, reason="buffer counts are a PostgreSQL executor property (test-postgres job)")
def test_one_question_stays_inside_its_database_budget(monkeypatch):
    from sqlalchemy import event, text

    from app.agents import runtime as rt
    from app.core.db import get_engine
    from app.core.rag import vector_store
    from app.core.rag.embeddings import get_embedder
    from tests.test_answer_path_query_plans import CORPUS, GK, OWN, _seed

    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", GK)
    monkeypatch.setenv("MASTER_CORPUS_SOURCE_PROJECT_ID", CORPUS)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")

    engine = get_engine()
    table = vector_store.get_store(dim=get_embedder().dim)._table_name
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
    asyncio.run(agent.chat("warm up the drainage works", project_id=OWN, user_id="u-budget"))

    statements: list = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        asyncio.run(agent.chat(QUESTION, project_id=OWN, user_id="u-budget"))
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    reads = plain_reads(statements)
    assert any(table in s for s, _ in reads), "the turn never read the chunk table"
    with engine.connect() as conn:
        data_bytes = int(conn.execute(text(f"SELECT pg_table_size('{table}')")).scalar())
    report = budget_report(reads, lambda s, p: _buffers(engine, s, p), table)
    broken = check_budget(report, data_bytes)
    assert not broken, "\n".join(broken)

    # Control: one full read of the table's text and embeddings -- what the
    # unbounded path effectively did -- breaks the per-statement bound.
    r, h = _buffers(engine, f"SELECT sum(length(text)), sum(vector_dims(embedding)) FROM {table}", None)
    assert (r + h) * PAGE > TABLE_SHARE * data_bytes, "control did not exceed the bound"
