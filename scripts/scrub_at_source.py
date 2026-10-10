#!/usr/bin/env python3
"""Rewrite matching SHARED-layer RAG rows in place. Counts only on stdout.

Rules are read from RAG_SCRUB_RULES (and RAG_SCRUB_EXTRA_TERMS) the same
way serve-time scrub reads them. This process never prints a rule, a
match, or row text.

Modes:
  count   — matches split by layer (shared general-knowledge vs project-own)
  clean   — requires confirm=CLEAN; rewrites shared matching rows only
  dryrun  — shared-layer detector count (serve-time list treated as empty)

Project-own rows are never rewritten. Shared = configured general-knowledge
project ids. Batched SQL UPDATE inside the DB; tsvector is generated;
embeddings are rebuilt only for changed ids via the app embedder.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Iterable, List, Sequence, Tuple

# Counts-only stdout. Nothing else goes to stdout.
_PREFIX = "SCRUB_AT_SOURCE"


def _emit(**fields: object) -> None:
    parts = [f"{key}={fields[key]}" for key in fields]
    print(f"{_PREFIX} {' '.join(parts)}", flush=True)


def _fail(code: str, **fields: object) -> int:
    _emit(error=code, **fields)
    return 2


def shared_project_ids() -> List[str]:
    from app.core.projects import general_knowledge_project_ids
    return sorted(general_knowledge_project_ids())


def _store():
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store
    emb = get_embedder()
    return get_store(dim=emb.dim), emb


def _rules():
    from app.core.identifier_scrub import _rules_from_env, compiled_rules
    raw = _rules_from_env()
    return raw, compiled_rules()


def _posix_pattern(python_pat: str) -> str:
    # Word boundary only. Other Python-only constructs are skipped by the
    # caller if Postgres rejects them — never logged.
    return python_pat.replace(r"\b", r"\y")


def _iter_project_rows(
    store, project_ids: Sequence[str], *, invert: bool, batch_size: int,
) -> Iterable[Tuple[str, str]]:
    """Yield (chunk_id, text) in id order. Caller must not print text."""
    from sqlalchemy import select

    cls = store._rag_chunk_cls
    last = ""
    ids = list(project_ids)
    while True:
        with store._lock:
            with store._session_factory()() as session:
                stmt = select(cls.chunk_id, cls.text).where(cls.chunk_id > last)
                if ids:
                    pred = cls.project_id.in_(ids)
                    stmt = stmt.where(~pred if invert else pred)
                elif not invert:
                    return
                stmt = stmt.order_by(cls.chunk_id).limit(batch_size)
                rows = session.execute(stmt).all()
        if not rows:
            return
        for row in rows:
            yield row.chunk_id, row.text
            last = row.chunk_id


def _count_python(store, project_ids: Sequence[str], *, invert: bool,
                  compiled, batch_size: int) -> int:
    n = 0
    for _cid, text in _iter_project_rows(
        store, project_ids, invert=invert, batch_size=batch_size,
    ):
        if text and any(pat.search(text) for pat, _ in compiled):
            n += 1
    return n


def _count_own_sql(store, shared_ids: Sequence[str], raw_rules, compiled) -> int:
    """In-DB count of project-own matches. Text never leaves the database."""
    from sqlalchemy import text as sql_text

    if not raw_rules:
        return 0
    table = store._table_name
    clauses: List[str] = []
    params: Dict[str, object] = {}
    for i, (pat, _repl) in enumerate(raw_rules):
        clauses.append(f"text ~* :p{i}")
        params[f"p{i}"] = _posix_pattern(pat)
    match = " OR ".join(clauses)
    if shared_ids:
        placeholders = ", ".join(f":s{i}" for i in range(len(shared_ids)))
        own = f"project_id NOT IN ({placeholders})"
        for i, sid in enumerate(shared_ids):
            params[f"s{i}"] = sid
    else:
        own = "TRUE"
    stmt = sql_text(
        f"SELECT COUNT(*) FROM {table} WHERE ({own}) AND ({match})"
    )
    with store._lock:
        with store._session_factory()() as session:
            try:
                return int(session.execute(stmt, params).scalar() or 0)
            except Exception:  # noqa: BLE001 — POSIX mismatch: fall back
                session.rollback()
    return _count_python(
        store, shared_ids, invert=True, compiled=compiled, batch_size=64,
    )


def _clean_shared(store, emb, shared_ids: Sequence[str], batch_size: int) -> Tuple[int, int]:
    from app.core.identifier_scrub import scrub_identifiers, text_matches_rules

    rewritten = 0
    reembedded = 0
    pending: List[Tuple[str, str]] = []

    def _flush(batch: List[Tuple[str, str]]) -> None:
        nonlocal rewritten, reembedded
        if not batch:
            return
        texts = [t for _cid, t in batch]
        vecs = emb.encode(texts)
        updates = [
            (cid, txt, vecs[i]) for i, (cid, txt) in enumerate(batch)
        ]
        n = store.rewrite_chunks(updates)
        rewritten += n
        reembedded += n

    for cid, text in _iter_project_rows(
        store, shared_ids, invert=False, batch_size=batch_size,
    ):
        if not text_matches_rules(text):
            continue
        new = scrub_identifiers(text)
        if new == text:
            continue
        pending.append((cid, new))
        if len(pending) >= batch_size:
            _flush(pending)
            pending = []
    _flush(pending)
    return rewritten, reembedded


def run(mode: str, confirm: str = "", batch_size: int = 64) -> Dict[str, int]:
    mode = (mode or "").strip().lower()
    if mode not in {"count", "clean", "dryrun"}:
        raise SystemExit(_fail("bad_mode"))
    if mode == "clean" and confirm != "CLEAN":
        raise SystemExit(_fail("confirm_required"))

    raw, compiled = _rules()
    counts = {
        "rules_loaded": len(raw),
        "shared_matches": 0,
        "project_own_matches": 0,
        "shared_rewritten": 0,
        "embeddings_updated": 0,
    }
    if not raw:
        _emit(mode=mode, **counts)
        return counts

    store, emb = _store()
    shared = shared_project_ids()

    if mode == "count":
        counts["shared_matches"] = _count_python(
            store, shared, invert=False, compiled=compiled, batch_size=batch_size,
        )
        if store._use_pgvector:
            counts["project_own_matches"] = _count_own_sql(
                store, shared, raw, compiled,
            )
        else:
            counts["project_own_matches"] = _count_python(
                store, shared, invert=True, compiled=compiled, batch_size=batch_size,
            )
        _emit(
            mode=mode,
            shared_matches=counts["shared_matches"],
            project_own_matches=counts["project_own_matches"],
            rules_loaded=counts["rules_loaded"],
        )
        return counts

    if mode == "dryrun":
        counts["shared_matches"] = _count_python(
            store, shared, invert=False, compiled=compiled, batch_size=batch_size,
        )
        _emit(
            mode=mode,
            shared_matches=counts["shared_matches"],
            rules_loaded=counts["rules_loaded"],
        )
        return counts

    # clean — shared layer only
    rewritten, reembedded = _clean_shared(store, emb, shared, batch_size)
    counts["shared_rewritten"] = rewritten
    counts["embeddings_updated"] = reembedded
    _emit(
        mode=mode,
        shared_rewritten=rewritten,
        embeddings_updated=reembedded,
        rules_loaded=counts["rules_loaded"],
    )
    return counts


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub matching shared-layer rows at source.")
    parser.add_argument("--mode", required=True, choices=("count", "clean", "dryrun"))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        run(args.mode, confirm=args.confirm, batch_size=max(1, args.batch_size))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
