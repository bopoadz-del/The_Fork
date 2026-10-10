#!/usr/bin/env python3
"""Clean the shared knowledge layer of what the serve-time scrub list matches.

Counts only on stdout. This process never prints a rule, a match or row text.

Modes:
  count   -- rows the rules match, by layer: shared general-knowledge versus
             each project's own documents; chunk text and document names
  clean   -- requires confirm=CLEAN; rewrites the matching SHARED rows in
             place and re-embeds only the rows that changed. A project's own
             rows are never written.
  dryrun  -- what the shared layer would expose with the serve-time list
             empty: the same match count, shared layer only. Zero is the bar
             for retiring RAG_SCRUB_RULES.

Shared = every project users other than its owner can read, by the
platform's own access rule (shared_project_ids). Everything else is a
project's own, seen only by that project's users, and is kept from other
projects by the retrieval scope (retriever.retrievable_project_ids).

On PostgreSQL (live) every match and every rewrite runs inside the database:
``~*`` to find rows, ``regexp_replace`` to rewrite them, in keyset batches by
chunk id. Only the ids of changed rows leave the database, and the text of
those rows alone is read back to re-embed them. A rule PostgreSQL cannot run
exactly as the scrubber does is refused (counted, never printed) -- the run
stops rather than reading rows out to match them elsewhere. The tsvector
column is GENERATED and recomputes on write.

On SQLite (local and tests) the same steps run in process: the file is local,
nothing crosses a network.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from pathlib import Path

# Run as ``python scripts/scrub_at_source.py``: the repository root, not
# scripts/, must be importable for ``app``.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PREFIX = "SCRUB_AT_SOURCE"
_DOCUMENTS = "documents"


def _emit(**fields: object) -> None:
    print(f"{_PREFIX} " + " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)


class Refused(Exception):
    """A stop the run reports by code and counts only."""

    def __init__(self, code: str, **fields: object) -> None:
        super().__init__(code)
        self.code, self.fields = code, fields


def shared_project_ids() -> List[str]:
    """Every project whose rows users other than its owner can read: the
    platform's own access rule (projects._is_shared_platform_grant --
    approved Drive-imported and boot-seeded corpora, and the configured
    general-knowledge ids). A corpus every user can open is shared whatever
    its id; reading only the general-knowledge list missed one."""
    from sqlalchemy import select

    from app.core import projects
    from app.core.models import Project

    ids = set(projects.general_knowledge_project_ids())
    projects._ensure_db()
    with projects.SessionLocal() as session:
        for row in session.scalars(select(Project)).all():
            if row.status != "archived" and projects._is_shared_platform_grant(row):
                ids.add(row.id)
    return sorted(ids)


def ordered_rules() -> List[Tuple[str, str]]:
    """The serve-time rules in the order the scrubber applies them
    (identifier_scrub._compiled: longest source pattern first)."""
    from app.core.identifier_scrub import _rules_from_env
    rules = _rules_from_env()
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return rules


# ── PostgreSQL translation ───────────────────────────────────────────────────
# Python's \b and \B are PostgreSQL's \y and \Y (\b there is a backspace).
_PY_ONLY = re.compile(r"\(\?P[<=]|\(\?[aiLmsux-]+[):]|\\[zZG]|[*+?}]\+")


def pg_pattern(pattern: str) -> Optional[str]:
    """The rule as a PostgreSQL ARE, or None when it uses Python-only syntax."""
    if _PY_ONLY.search(pattern):
        return None
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            out.append({"b": r"\y", "B": r"\Y"}.get(nxt, ch + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def pg_replacement(repl: str) -> Optional[str]:
    """Plain replacement text only: a backreference (or any backslash) means
    the two engines could disagree, so the rule is refused."""
    return None if "\\" in repl else repl


# ── stores ───────────────────────────────────────────────────────────────────
def _store():
    """The vector store and embedder, for re-embedding changed rows only."""
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store
    emb = get_embedder()
    return get_store(dim=emb.dim), emb


class _Db:
    """The chunk table and a session on the configured database -- no
    embedder: counting and rewriting text need none, and loading one on a
    runner is a model download the count does not need."""

    def __init__(self) -> None:
        from app.core.db import _session_factory_for_url
        from app.core.models import rag_chunk_table_name
        from app.core.rag.vector_store import (
            _database_url, _default_db_path, _rag_vector_namespace,
        )
        url = _database_url(_default_db_path())
        self._factory = _session_factory_for_url(url)
        self.is_postgres = url.startswith("postgresql")
        self.table = rag_chunk_table_name(_rag_vector_namespace())

    def session(self):
        return self._factory()


def _session(db):
    return db.session()


def _projects_session():
    from app.core import projects
    projects._ensure_db()
    return projects.SessionLocal()


class Target:
    """One stored text column: where it lives and how its rows are keyed."""

    def __init__(self, open_session, table: str, key: str, column: str) -> None:
        self.open_session, self.table, self.key, self.column = open_session, table, key, column


def _pg_rules(rules: Sequence[Tuple[str, str]], db) -> List[Tuple[str, str]]:
    """Translate every rule and let PostgreSQL compile it. Any refusal stops
    the run with a count."""
    from sqlalchemy import text as sql

    out, refused = [], 0
    with _session(db) as session:
        for pat, repl in rules:
            p, r = pg_pattern(pat), pg_replacement(repl)
            if p is None or r is None:
                refused += 1
                continue
            try:
                session.execute(sql("SELECT '' ~* :p"), {"p": p})
            except Exception:  # noqa: BLE001 -- counted, never logged
                session.rollback()
                refused += 1
                continue
            out.append((p, r))
    if refused:
        raise Refused("rules_unsupported", rules_unsupported=refused, rules_loaded=len(rules))
    return out


def _pg_count(t: Target, rules, shared: Sequence[str]) -> Tuple[int, int]:
    """(shared rows, project-own rows) whose ``column`` any rule matches --
    counted in the database."""
    from sqlalchemy import text as sql

    params: Dict[str, object] = {"shared": list(shared)}
    match = []
    for i, (p, _r) in enumerate(rules):
        params[f"p{i}"] = p
        match.append(f"{t.column} ~* :p{i}")
    stmt = sql(
        f"SELECT COUNT(*) FILTER (WHERE project_id = ANY(:shared)), "
        f"COUNT(*) FILTER (WHERE NOT (project_id = ANY(:shared))) "
        f"FROM {t.table} WHERE {' OR '.join(match)}"
    )
    with t.open_session() as session:
        row = session.execute(stmt, params).one()
    return int(row[0] or 0), int(row[1] or 0)


def _pg_rewrite(t: Target, rules, shared: Sequence[str], batch: int) -> Set[str]:
    """Rewrite ``column`` of shared rows, one rule at a time in the scrubber's
    order, in keyset batches by key (each row is visited once per rule, so a
    replacement that still matches cannot loop). Keys compare in byte order
    (COLLATE "C") so the batch boundary means the same in Python and in the
    database. Returns the changed keys."""
    from sqlalchemy import text as sql

    changed: Set[str] = set()
    key, col = t.key, t.column
    for p, r in rules:
        last = ""
        while True:
            stmt = sql(
                f"WITH picked AS (SELECT {key} FROM {t.table} "
                f"  WHERE project_id = ANY(:shared) AND {key} COLLATE \"C\" > :last "
                f"  AND {col} ~* :p ORDER BY {key} COLLATE \"C\" LIMIT :n) "
                f"UPDATE {t.table} AS tgt SET {col} = regexp_replace(tgt.{col}, :p, :r, 'gi') "
                f"FROM picked WHERE tgt.{key} = picked.{key} RETURNING tgt.{key}"
            )
            with t.open_session() as session:
                ids = [str(x) for x in session.execute(
                    stmt, {"shared": list(shared), "last": last, "p": p, "r": r, "n": batch},
                ).scalars()]
                session.commit()
            if not ids:
                break
            changed.update(ids)
            last = max(ids)
    return changed


# ── SQLite (local) ───────────────────────────────────────────────────────────
def _local_rows(t: Target) -> Iterable[Tuple[str, str, str]]:
    from sqlalchemy import text as sql
    with t.open_session() as session:
        return session.execute(sql(f"SELECT {t.key}, project_id, {t.column} FROM {t.table}")).all()


def _local_count(t: Target, compiled, shared) -> Tuple[int, int]:
    s = o = 0
    for _k, pid, value in _local_rows(t):
        if value and any(p.search(value) for p, _ in compiled):
            if pid in shared:
                s += 1
            else:
                o += 1
    return s, o


def _local_rewrite(t: Target, compiled, shared) -> Set[str]:
    from sqlalchemy import text as sql
    changed: Set[str] = set()
    for k, pid, value in _local_rows(t):
        if pid not in shared or not value:
            continue
        new = value
        for pat, repl in compiled:
            new = pat.sub(repl, new)
        if new != value:
            with t.open_session() as session:
                session.execute(sql(f"UPDATE {t.table} SET {t.column} = :v WHERE {t.key} = :k"),
                                {"v": new, "k": k})
                session.commit()
            changed.add(str(k))
    return changed


def _reembed(ids: Sequence[str], batch: int) -> int:
    """New embeddings for the changed chunks only, read back by id."""
    from sqlalchemy import select

    if not ids:
        return 0
    store, emb = _store()
    cls = store._rag_chunk_cls
    ids = sorted(ids)
    done = 0
    for i in range(0, len(ids), batch):
        part = ids[i:i + batch]
        with store._session_factory()() as session:
            rows = session.execute(select(cls.chunk_id, cls.text).where(cls.chunk_id.in_(part))).all()
        vecs = emb.encode([r.text for r in rows])
        done += store.rewrite_chunks([(r.chunk_id, r.text, vecs[j]) for j, r in enumerate(rows)])
    return done


# ── run ──────────────────────────────────────────────────────────────────────
def run(mode: str, confirm: str = "", batch_size: int = 200) -> Dict[str, int]:
    mode = (mode or "").strip().lower()
    if mode not in {"count", "clean", "dryrun"}:
        raise Refused("bad_mode")
    if mode == "clean" and confirm != "CLEAN":
        raise Refused("confirm_required")

    rules = ordered_rules()
    counts = {"rules_loaded": len(rules)}
    if not rules:
        _emit(mode=mode, **counts)
        return counts

    db = _Db()
    shared = shared_project_ids()
    chunks = Target(db.session, db.table, "chunk_id", "text")
    names = Target(_projects_session, _DOCUMENTS, "id", "original_name")

    if db.is_postgres:
        pg = _pg_rules(rules, db)

        def count(target):
            return _pg_count(target, pg, shared)

        def rewrite(target):
            return _pg_rewrite(target, pg, shared, batch_size)
    else:
        compiled = [(re.compile(p, re.IGNORECASE), r) for p, r in rules]

        def count(target):
            return _local_count(target, compiled, set(shared))

        def rewrite(target):
            return _local_rewrite(target, compiled, set(shared))

    if mode in ("count", "dryrun"):
        cs, co = count(chunks)
        ns, no = count(names)
        counts.update(shared_projects=len(shared), shared_matches=cs, shared_document_names=ns)
        if mode == "count":
            counts.update(project_own_matches=co, project_own_document_names=no)
        _emit(mode=mode, **counts)
        return counts

    changed = rewrite(chunks)
    renamed = rewrite(names)
    counts.update(shared_rewritten=len(changed), shared_documents_renamed=len(renamed),
                  embeddings_updated=_reembed(sorted(changed), batch_size))
    _emit(mode=mode, **counts)
    return counts


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean the shared layer at source (counts only).")
    parser.add_argument("--mode", required=True, choices=("count", "clean", "dryrun"))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        run(args.mode, confirm=args.confirm, batch_size=max(1, args.batch_size))
    except Refused as exc:
        _emit(error=exc.code, **exc.fields)
        return 2
    except Exception as exc:  # noqa: BLE001 -- the type only: a message could quote a row
        # The type only: a message could quote a row. A missing module's name
        # is code, not data, so it is shown.
        missing = getattr(exc, "name", None) if isinstance(exc, ImportError) else None
        _emit(error="failed", type=type(exc).__name__, **({"module": missing} if missing else {}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
