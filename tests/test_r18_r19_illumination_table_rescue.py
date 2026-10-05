"""R18/R19: the work-activity illumination table reaches the model.

Live on 4bbb632, both 0/6. R18 ("Per the project HSE lighting requirements,
what minimum illumination is required for concrete placement during night
work?" — 50 lux) and R19 ("… for bricklaying?" — 100 lux) refused: the query's
"HSE lighting / night work" wording steered hybrid retrieval to the HSE plan
and a pre-condition site-survey .docx, and the spec's illumination table never
entered top-k — even though its BM25 tokens ("concrete placement",
"bricklaying", "illumination", "lux") are right there.

Corpus truth (made the product quote it, doc e6e0702b chunk 661 / 9a56fb14
chunk 73, Vol 2 - Specification (6 of 9).pdf, DD-SWD-…-HS-000002 Rev01):

    The following table indicates the minimum levels of area illumination
    required for the type of work indicated.
    | Work Activity | LUX | Foot Candle |
    | Interior movement only | 10 | 1.0 |
    | Handling material | 30 | 3.0 |
    | Interior reinforcing | 50 | 5.0 |
    | Concrete placement | 50 | 5.0 |
    | Bricklaying | 100 | 10.0 |
    | Office lighting | 100 | 10.0 |
    | Bench work/plastering | 200 | 20.0 | …

The MEP room table (chunks 435/437: "Service Luminance — Avg Lux",
"Control Rooms 500", "Uo (uniformity)") is a DIFFERENT table and must not be
mistaken for this one.

Fix: a targeted retrieval rescue (always on)
that pools the work-activity illumination table when an illumination-for-an-
activity ask has no lux row in top-k — same shape as the soil-contact-cover
rescue. Synthetic text throughout.
"""
from __future__ import annotations

import logging

from app.core.rag import retriever as ret
from app.core.rag.vector_store import Chunk

WORK_ACTIVITY_TABLE = (
    "The following table indicates the minimum levels of area illumination "
    "required for the type of work indicated. "
    "| Work Activity | LUX | Foot Candle | "
    "| Interior movement only | 10 | 1.0 | "
    "| Handling material | 30 | 3.0 | "
    "| General rough work | 30 | 3.0 | "
    "| Interior reinforcing | 50 | 5.0 | "
    "| Concrete placement | 50 | 5.0 | "
    "| Bricklaying | 100 | 10.0 | "
    "| Office lighting | 100 | 10.0 | "
    "| Bench work/plastering | 200 | 20.0 |"
)
# The MEP room table — same corpus, different table, room lux not activity lux.
MEP_ROOM_TABLE = (
    "Table 12-1: Lighting levels | Location | Service Luminance - Avg Lux | "
    "Uo (uniformity) | Control Rooms | 500 | 0.4 | Plant rooms | 200 | 0.4 | "
    "Corridors | 150 | 0.4 |"
)
HSE_PROSE = (
    "The Contractor shall provide lighting installations giving minimum "
    "illumination levels as per standard design, arranged so all areas "
    "receive light from at least two directions to prevent shadows."
)

R18 = ("Per the project HSE lighting requirements, what minimum illumination "
       "is required for concrete placement during night work?")
R19 = ("Per the project HSE lighting requirements, what minimum illumination "
       "is required for bricklaying?")


# ── the ask is recognised, unrelated asks are not ──────────────────────────

def test_r18_r19_are_recognised_as_illumination_level_asks():
    assert ret.query_asks_illumination_level(R18)
    assert ret.query_asks_illumination_level(R19)


def test_unrelated_asks_do_not_trigger_the_rescue():
    for q in (
        "What is the Defects Notification Period under this contract?",
        "How much concrete for 24 pile caps 2.5 x 2.5 x 1.2 m?",
        "What is the Time for Completion for the whole of the Works?",
    ):
        assert not ret.query_asks_illumination_level(q), q


# ── the table is recognised, the MEP room table and prose are not ──────────

def test_the_work_activity_table_is_recognised():
    assert ret.chunk_states_work_activity_illumination(WORK_ACTIVITY_TABLE)


def test_the_mep_room_table_is_not_the_work_activity_table():
    assert not ret.chunk_states_work_activity_illumination(MEP_ROOM_TABLE)


def test_hse_prose_without_a_lux_figure_is_not_the_table():
    assert not ret.chunk_states_work_activity_illumination(HSE_PROSE)


# ── the rescue pools the table when top-k lacks it ─────────────────────────

class _FakeStore:
    """Only what the rescue calls: chunks_containing_all(pid, needles, k)."""

    def __init__(self, chunks):
        self._chunks = chunks

    def chunks_containing_all(self, project_id, needles, k=20, doc_ids=None):
        nl = [str(n).lower() for n in (needles or [])]
        out = []
        for c in self._chunks:
            if c.project_id != project_id:
                continue
            t = (c.text or "").lower()
            if nl and all(n in t for n in nl):
                out.append(c)
        return out[: max(1, int(k or 20))]


def _chunk(cid, text, pid="P"):
    return Chunk(chunk_id=cid, project_id=pid, doc_id=cid, chunk_index=0,
                 text=text, score=0.0, source_name="")


def test_rescue_pools_the_table_when_top_k_has_only_hse_prose():
    table = _chunk("spec-661", WORK_ACTIVITY_TABLE)
    store = _FakeStore([table, _chunk("mep-435", MEP_ROOM_TABLE)])
    # top-k (fused) has only the HSE prose chunk — no lux row.
    prose = _chunk("hse-1", HSE_PROSE)
    fused = {"hse-1": (prose, 0.62, 0.0)}
    added = ret._rescue_illumination_table_chunks(R18, "P", fused, store)
    assert added >= 1
    assert "spec-661" in fused
    # the MEP room table is not pooled by this rescue
    assert "mep-435" not in fused


def test_rescue_is_a_noop_for_an_unrelated_ask():
    store = _FakeStore([_chunk("spec-661", WORK_ACTIVITY_TABLE)])
    fused = {}
    added = ret._rescue_illumination_table_chunks(
        "What is the Defects Notification Period?", "P", fused, store,
    )
    assert added == 0
    assert fused == {}


def test_rescue_scans_master_corpus_source_pid_not_just_the_ui_pid(monkeypatch):
    """Live 54d017c: attempt 1 found nothing because on master_corpus the table
    chunk is owned by a SOURCE project id, not the UI project id the query runs
    under — chunks_containing_all(ui_pid) returned nothing. The rescue must scan
    the source pids (via _late_scan_project_ids), like the E1 rescue does."""
    UI = "master_corpus"
    SRC = "src-corpus-1"
    # the table lives under the SOURCE pid, never under the UI pid
    table = _chunk("spec-661", WORK_ACTIVITY_TABLE, pid=SRC)
    store = _FakeStore([table])
    monkeypatch.setattr(ret, "_late_scan_project_ids",
                        lambda pid, *a, **k: [pid, SRC])
    fused = {}
    added = ret._rescue_illumination_table_chunks(R18, UI, fused, store)
    assert added >= 1
    assert "spec-661" in fused


def test_rescue_reaches_a_general_knowledge_pid_only_via_extra_pids(monkeypatch):
    """Attempts 1-2 (0/6 live): _late_scan_project_ids does NOT include the
    general-knowledge pids (gk_ids) that the semantic leg searches and that
    RAG_GENERAL_KNOWLEDGE_PROJECTS holds in prod (two projects). The spec table
    lives under a GK pid; only extra_pids (= gk_ids + fb_id, passed by the call
    site) reaches it. Stub _late_scan_project_ids with its real contract: UI pid
    plus whatever extra_pids the caller threads."""
    monkeypatch.setattr(ret, "_late_scan_project_ids",
                        lambda pid, extra=None, fused=None: [pid] + list(extra or []))
    UI = "master_corpus"
    GK = "gk-project-1"
    table = _chunk("spec-661", WORK_ACTIVITY_TABLE, pid=GK)
    store = _FakeStore([table])
    # Without extra_pids the GK pid is never scanned — the attempt-2 bug.
    f_without = {}
    ret._rescue_illumination_table_chunks(R18, UI, f_without, store)
    assert "spec-661" not in f_without
    # With extra_pids carrying the GK pid, the table is pooled.
    f_with = {}
    added = ret._rescue_illumination_table_chunks(
        R18, UI, f_with, store, [GK],
    )
    assert added >= 1
    assert "spec-661" in f_with


# ── observability: the diagnostic must reach CloudWatch, which carries only
# WARNING+ from plain module loggers (prod root logger is at WARNING — the
# setup_structured_logging NOTSET guard never fires). Attempts 1-3 logged at
# INFO and were invisible; "no rescue log" proved nothing. These pin the level.
#
# The diagnostic must be logger.WARNING, not .info — prod's root logger is at
# WARNING so a module INFO line never reaches CloudWatch (attempts 1-3 logged
# at INFO and were invisible). We capture by spying ret.logger.warning: caplog
# and even a per-logger handler both miss this line in the full suite once an
# earlier test has replaced root handlers or raised logging.disable (the suite
# does both — see test_walk_junk_filter / test_r2_archive_ingest_survives).
# Spying the bound method is immune to handler, level, and global-disable state.

def _spy_warning(monkeypatch):
    lines: list[str] = []

    def _w(msg, *args, **kwargs):
        try:
            lines.append(msg % args if args else str(msg))
        except (TypeError, ValueError):
            lines.append(str(msg))

    monkeypatch.setattr(ret.logger, "warning", _w)
    return lines


def test_diagnostic_is_logged_at_warning_when_nothing_is_admitted(monkeypatch):
    lines = _spy_warning(monkeypatch)
    store = _FakeStore([])  # no table anywhere -> admitted 0
    ret._rescue_illumination_table_chunks(R18, "P", {}, store)
    hit = [m for m in lines if "illumination-table rescue:" in m]
    assert hit, "the always-on diagnostic must log at WARNING (visible in prod)"
    assert "admitted=0" in hit[0]
    assert "needle_hits=" in hit[0]


def test_diagnostic_is_not_logged_at_info():
    # Guard the whole point: the line must NOT be an info() call (invisible in
    # prod). If someone reverts it to logger.info, spying warning misses it.
    import inspect
    src = inspect.getsource(ret._rescue_illumination_table_chunks)
    assert "logger.info(" not in src, "the rescue diagnostic must not use logger.info"
    assert src.count("logger.warning(") >= 2


def test_pooled_diagnostic_is_logged_at_warning(monkeypatch):
    lines = _spy_warning(monkeypatch)
    store = _FakeStore([_chunk("spec-661", WORK_ACTIVITY_TABLE)])
    ret._rescue_illumination_table_chunks(R18, "P", {}, store)
    msgs = " || ".join(lines)
    assert "illumination-table rescue:" in msgs
    assert "pooled 1 chunk" in msgs


# ── Task 1B: the pooled table chunks lose the k-cut (live: admitted=4, still
# 0/6). Give the illumination ask extra retrieval slots, exactly like the
# spec-deferred-cover ask does (_SPEC_DEFERRED_COVER_EXTRA_K).

def test_illumination_ask_gets_extra_retrieval_slots():
    from app.core.rag.inject import rag_retrieval_k
    assert rag_retrieval_k(R18, 5) == 7
    assert rag_retrieval_k(R19, 5) == 7
    # unrelated asks keep RAG_K
    assert rag_retrieval_k("What is the site address?", 5) == 5
    assert rag_retrieval_k("What is the Defects Notification Period?", 5) == 5


# ── Task 1 (bonus): the pooled table loses the k-cut because it pools at bonus
# 0.0 and the cut sorts fused by sem+bonus. Give the two highest-cosine table
# chunks a bonus so they enter top-k. (admitted=4 live, extra-k alone = R19 1/6.)

def _cos_by_marker(_embedder, _qv, texts):
    import re
    out = []
    for t in texts:
        m = re.search(r"COSKEY (\d+)", t)
        out.append((int(m.group(1)) / 10.0) if m else 0.0)
    return out


def _table(cid, cosmark):
    return _chunk(cid, WORK_ACTIVITY_TABLE + f" COSKEY {cosmark}")


def test_rescue_bonuses_the_two_highest_cosine_table_chunks(monkeypatch):
    monkeypatch.setattr(ret, "_cosine_to_query", _cos_by_marker)
    store = _FakeStore([_table("spec-0", 0), _table("spec-1", 1),
                        _table("spec-2", 2), _table("spec-3", 3)])
    fused = {}
    ret._rescue_illumination_table_chunks(
        R18, "P", fused, store, embedder=object(), query_vec=[1.0],
    )
    bonus = {cid: e[2] for cid, e in fused.items()}
    bonused = sorted(c for c, b in bonus.items() if b >= 2.0)
    assert bonused == ["spec-2", "spec-3"], bonus       # the two highest cosine
    assert bonus["spec-0"] == 0.0 and bonus["spec-1"] == 0.0


def test_rescue_never_lowers_an_existing_bonus(monkeypatch):
    monkeypatch.setattr(ret, "_cosine_to_query", _cos_by_marker)
    pre = _table("spec-0", 0)
    fused = {"spec-0": (pre, 0.4, 3.0)}  # already pooled with a higher bonus
    store = _FakeStore([pre, _table("spec-1", 1), _table("spec-2", 2)])
    ret._rescue_illumination_table_chunks(
        R18, "P", fused, store, embedder=object(), query_vec=[1.0],
    )
    assert fused["spec-0"][2] == 3.0  # untouched, not lowered to 2.0 or 0.0


def test_unrelated_ask_applies_no_bonus(monkeypatch):
    monkeypatch.setattr(ret, "_cosine_to_query", _cos_by_marker)
    store = _FakeStore([_table("spec-0", 0)])
    fused = {}
    ret._rescue_illumination_table_chunks(
        "What is the Defects Notification Period?", "P", fused, store,
        embedder=object(), query_vec=[1.0],
    )
    assert fused == {}


def test_diagnostic_reports_bonus_applied(monkeypatch):
    monkeypatch.setattr(ret, "_cosine_to_query", _cos_by_marker)
    lines = _spy_warning(monkeypatch)
    store = _FakeStore([_table("spec-0", 0), _table("spec-1", 1),
                        _table("spec-2", 2), _table("spec-3", 3)])
    ret._rescue_illumination_table_chunks(
        R18, "P", {}, store, embedder=object(), query_vec=[1.0],
    )
    diag = [m for m in lines if "illumination-table rescue:" in m]
    assert diag and "bonus_applied=2" in diag[0], diag
