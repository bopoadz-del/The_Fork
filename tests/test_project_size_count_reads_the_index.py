"""The bounded project-size count reads the (project_id, doc_id) index, not
the table -- even when the planner would otherwise gamble on a sequential
scan (a project holding most rows, wide rows, a visibility map not yet set).
PostgreSQL only; synthetic rows."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.getenv("PYTEST_USE_POSTGRES", "").strip().lower() not in ("1", "true", "yes"),
    reason="query plans are a PostgreSQL planner property (test-postgres job)",
)


def _nodes(plan):
    stack = [plan]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.get("Plans") or [])


@pytest.mark.parametrize("share", [60, 95, 99])
def test_the_count_never_scans_the_table(share):
    from app.core.db import get_engine
    from app.core.rag.vector_store import _EXACT_SCAN_MAX_ROWS, _PROJECT_ROWS_SQL

    table = f"qp_size_probe_{share}"
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        conn.execute(text(f"CREATE TABLE {table} (chunk_id text PRIMARY KEY, project_id text, "
                          "doc_id text, body text)"))
        conn.execute(text(
            f"INSERT INTO {table} SELECT 'c' || g, CASE WHEN g % 100 < :share THEN 'big' "
            "ELSE 'p' || (g % 37) END, 'd' || (g % 50), repeat('x', 1500) "
            "FROM generate_series(1, 30000) g"), {"share": share})
        conn.execute(text(f"CREATE INDEX ON {table} (project_id)"))
        conn.execute(text(f"CREATE INDEX ON {table} (project_id, doc_id)"))
        conn.execute(text(f"ANALYZE {table}"))  # statistics, but no VACUUM: no all-visible pages
    try:
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            sql = _PROJECT_ROWS_SQL.format(table=table).replace(":p", "%(p)s").replace(":m", "%(m)s")
            cur.execute("EXPLAIN (FORMAT JSON) " + sql, {"p": "big", "m": _EXACT_SCAN_MAX_ROWS + 1})
            plan = cur.fetchone()[0]
            plan = plan[0]["Plan"] if isinstance(plan, list) else plan
            seq = [n for n in _nodes(plan) if n.get("Node Type") == "Seq Scan"]
            assert seq == [], plan
        finally:
            raw.rollback()
            raw.close()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
