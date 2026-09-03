"""An unnamed contract question is won by evidence, not by arrival order.

Live wave-1 on the Master Corpus, SHA ``567147a``, measured five Contract
Data questions against one corpus in one session and got both outcomes:

* **A2** (Accepted Contract Amount incl. VAT) — PASS
* **A6** (Defects Notification Period) — PASS
* **A3** (Time for Completion, whole of the Works) — FAIL, ``the retrieved
  excerpts do not contain`` it
* **A5** (Delay Damages, whole of the Works) — FAIL, cited **Sub-Clause
  8.8** and then reported the rate and the cap as absent from its excerpts
* **A9** (who the Engineer is) — FAIL_WRONG_CONTRACT, answered out of a
  **different contract year's** Conditions of Contract with nothing but the
  generic defined-term entry

A2 and A6 prove the Contract Data is indexed and retrievable, so the three
failures are not a corpus gap. What separates them is which chunk happened
to sort first: the unnamed fence locks the pool to the first candidate
carrying a ``PREFIX-YEAR-SEQ`` id and DROPS every other id, so a wrong-year
chunk at rank 1 does not merely outrank the row holding the answer — it
deletes it, at every rank. A2/A6 won that race; A3/A5/A9 lost it.

Two things decide the race wrongly, and both are pinned below.

1. A General Conditions clause that says *"at the rate stated in the
   Contract Data"* is a **pointer** to the answer. It carries the heading
   phrase and, being a clause about delay damages, repeats every label word
   the question uses — so it collected the Contract Data heading tier plus
   the full label bonus and beat the row that actually states the rate.
   That is A5's symptom exactly: cite 8.8, then report the rate as missing.
2. *"Who is the Engineer"* was never recognised as asking for a filled
   particular at all, because a particular was assumed to be a figure. No
   part of the particulars machinery ran on A9, so a glossary definition
   won on raw cosine — and locked its own contract year.

SANITIZED FIXTURE. The two contract/document ids are the live ones and are
already in git (``tests/test_cross_contract_fence.py``, ``AGENTS.md``);
every figure, party and value here is fixture-only and deliberately differs
from the live pack, which must never land in the repository. What is under
test is the mechanism — which contract's Contract Data rows reach the
top-k — not the live numbers.
"""
from __future__ import annotations

import pytest

from app.core.contract_data_chunks import contract_data_particulars_chunks
from app.core.rag.retriever import (
    contract_data_mention_is_only_a_cross_reference,
    contract_data_particulars_delta,
    elect_answer_bearing_contract,
    is_contract_data_particulars_row,
    query_asks_for_contract_particulars,
)

DD23_NAME = (
    "DD-2023-118_the client project II Infrastructure Package 1_"
    "Vol 1 - Conditions of Contract.pdf"
)
DD22_NAME = "DD-2022-175 - Volume 1 - Conditions of Contract.pdf"
KB_NAME = "fidic_2017_administration.md"

PROJECT = "master_corpus"
GK_PROJECT = "training_material"

# ── the asks, verbatim from the frozen battery ────────────────────────────
A2 = "What is the Accepted Contract Amount including VAT?"
A3 = "What is the Time for Completion for the whole of the Works?"
A5 = "What are the Delay Damages for the whole of the Works?"
A6 = "What is the Defects Notification Period?"
A9 = "Who is the Engineer under this contract?"

# ── DD-2023-118: the executed contract's own Contract Data (sanitized) ────
DD23_CONTRACT_DATA = """Volume 1 - Conditions of Contract

Particular Conditions Part A - Contract Data

1.1.1 Accepted Contract Amount excluding VAT | SAR 8,640,000.00
1.1.1 Accepted Contract Amount including VAT | SAR 9,936,000.00
1.1.27 Defects Notification Period | 365 days from the Taking-Over Certificate
1.1.67 Sections | Not Applicable
1.1.75 Time for Completion for the whole of the Works | 640 days
1.3.1 (a) Approved method of electronic communication | ProjectMail
1.3.1 (b) Engineer | Northwater Engineers (Demo Saudi Limited)
4.3.3 Performance Bond | 10% of the Accepted Contract Amount
8.8 Delay Damages for the whole of the Works | 0.1% of the Contract Price per calendar day
8.8 Maximum amount of delay damages | 10% of the Accepted Contract Amount
"""

# The values the three failing asks must reach, as the indexer writes them.
A3_ANSWER = "640 days"
A5_ANSWER = "0.1% of the Contract Price per calendar day"
A9_ANSWER = "Northwater Engineers"

# ── DD-2022-175: another year's Conditions of Contract. Every one of these
# points AT the Contract Data instead of stating a particular, which is what
# a General Conditions clause does.
DD22_TFC_CLAUSE = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.2 Time for Completion. "
    "The Contractor shall complete the whole of the Works within the Time "
    "for Completion for the whole of the Works stated in the Contract Data, "
    "calculated from the Commencement Date, being 1,095 days as extended "
    "under Sub-Clause 8.5 Extension of Time for Completion."
)
DD22_DELAY_CLAUSE = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.8 Delay Damages. If "
    "the Contractor fails to comply with Sub-Clause 8.2, the Contractor "
    "shall pay delay damages for the whole of the Works at the rate stated "
    "in the Contract Data for every calendar day which shall elapse between "
    "the relevant Time for Completion and the date stated in the Taking-Over "
    "Certificate, up to the maximum amount of delay damages stated in the "
    "Contract Data, being 10% of the Accepted Contract Amount."
)
DD22_ENGINEER_DEFINITION = (
    'Volume 1 - Conditions of Contract. 1.1.35 "Engineer" means the person '
    "appointed by the Employer to act as the Engineer for the purposes of "
    "the Contract and named in the Contract Data, or any replacement "
    "appointed under Sub-Clause 3.6 within 28 days."
)
# The other year's Contract Data table as a scanned tender volume yields it:
# the keys survived, the value column did not. It is a real particulars
# window, so it collects the index-time prefix bonus, and its keys name every
# label the three questions use, so it collects the full label bonus too — a
# combination that out-scores the filled row it should never be preferred to.
# It states nothing, which is exactly why the election must skip it.
DD22_UNFILLED_CONTRACT_DATA = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage "
    f"[{DD22_NAME}].\n"
    "Contract Data\n"
    "1.1.1 Accepted Contract Amount excluding VAT\n"
    "1.1.27 Defects Notification Period\n"
    "1.1.75 Time for Completion for the whole of the Works\n"
    "1.3.1 (b) Engineer\n"
    "8.8 Delay Damages for the whole of the Works"
)

# ── the curated FIDIC knowledge base (#468's F-KB-1). Background, never the
# contract — and it must not take a top-k slot from the row that answers.
KB_DELAY_NOTE = (
    "FIDIC 2017 contract administration notes. Delay damages under "
    "Sub-Clause 8.8 are payable for the whole of the Works at the rate "
    "stated in the Contract Data, commonly 0.05% of the Contract Price per "
    "calendar day, capped at 10% of the Accepted Contract Amount. The "
    "Engineer certifies the Time for Completion under Sub-Clause 8.2."
)

DD22_DOCS = {
    "dd22_tfc": DD22_TFC_CLAUSE,
    "dd22_delay": DD22_DELAY_CLAUSE,
    "dd22_engineer": DD22_ENGINEER_DEFINITION,
    "dd22_unfilled_cd": DD22_UNFILLED_CONTRACT_DATA,
}


def _doc_names() -> dict:
    names = {doc_id: DD22_NAME for doc_id in DD22_DOCS}
    names["kb_delay"] = KB_NAME
    return names


def _dd23_delay_damages_row() -> str:
    """A5's row as the indexer writes it — taken from the indexer itself, so
    a change to the chunk format cannot leave this fixture behind."""
    return next(
        r for r in contract_data_particulars_chunks(
            DD23_CONTRACT_DATA, filename=DD23_NAME,
        )
        if A5_ANSWER in r
    )


@pytest.fixture
def two_year_corpus(tmp_path, monkeypatch):
    """One project holding two contract years, plus the curated FIDIC KB.

    Mirrors the live Master Corpus: the DD-2023-118 executed contract's
    Contract Data is indexed through the REAL particulars chunker, so this
    fixture cannot drift from the format the indexer emits.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", GK_PROJECT)
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_CD_PARTICULARS_BOOST", raising=False)

    from sqlalchemy import delete as _sa_delete

    from app.core.rag import embeddings as _emb
    from app.core.rag import retriever as ret
    from app.core.rag import vector_store as _vs

    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store

    embedder = Embedder(model_name="fake")
    store = get_store(dim=embedder.dim)
    with store._lock, store._session_factory()() as session:
        session.execute(_sa_delete(store._rag_chunk_cls))
        session.commit()

    names = _doc_names()

    cd_chunks = contract_data_particulars_chunks(
        DD23_CONTRACT_DATA, filename=DD23_NAME,
    )
    assert cd_chunks, "the particulars chunker produced nothing to retrieve"
    for i, chunk in enumerate(cd_chunks):
        doc_id = f"dd23_cd_{i}"
        names[doc_id] = DD23_NAME
        store.upsert_chunks(PROJECT, doc_id, [chunk], embedder.encode([chunk]))

    for doc_id, text in DD22_DOCS.items():
        store.upsert_chunks(PROJECT, doc_id, [text], embedder.encode([text]))
    store.upsert_chunks(
        GK_PROJECT, "kb_delay", [KB_DELAY_NOTE], embedder.encode([KB_DELAY_NOTE]),
    )

    monkeypatch.setattr(ret, "_doc_name_for_id", lambda did: names.get(did, ""))

    # The live ranking condition. A Conditions of Contract clause repeats the
    # question's wording many times over; a one-line particulars row states
    # it once. The clause therefore leads on raw cosine, which is what put a
    # wrong-year chunk at rank 1 and handed it the whole pool.
    real_search = store.search

    def wrong_year_leads(project_id, query_vec, k=20, query_text=None):
        hits = real_search(project_id, query_vec, k=k, query_text=query_text)
        for chunk in hits:
            if chunk.doc_id in DD22_DOCS:
                chunk.score = 0.91
            elif chunk.doc_id == "kb_delay":
                chunk.score = 0.88
            else:
                chunk.score = 0.55
        return hits

    monkeypatch.setattr(store, "search", wrong_year_leads)

    yield ret, names

    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


def _top_k(ret, names, ask, k=5):
    chunks, _ = ret.retrieve_with_filter(ask, PROJECT, k=k)
    return [(names.get(c.doc_id, ""), c.text or "") for c in chunks]


def _blob(top):
    return "\n".join(text for _name, text in top)


def _report(top):
    return "\n".join(
        f"  {i}. {name[:24]:<26} {text[:88]!r}"
        for i, (name, text) in enumerate(top, 1)
    )


def _assert_own_contract_data_leads(top, answer):
    """The three properties the live failures each violated."""
    assert top, "nothing retrieved — the answer row is in the fixture"
    assert answer in _blob(top), (
        f"{answer!r} never reached the top-5:\n" + _report(top)
    )
    lead_name, lead_text = top[0]
    assert lead_name == DD23_NAME, (
        "rank 1 is not the contract being asked about:\n" + _report(top)
    )
    assert lead_text.startswith("CONTRACT DATA particulars"), (
        "rank 1 is not a Contract Data row:\n" + _report(top)
    )
    assert DD22_NAME not in [name for name, _t in top], (
        "another contract year is in the top-5:\n" + _report(top)
    )


# ── the three regressions, at production k=5 ──────────────────────────────


def test_a3_time_for_completion_reaches_the_dd2023_particulars_row(two_year_corpus):
    """A3 said the excerpts did not contain it. The row must lead the top-5."""
    ret, names = two_year_corpus
    top = _top_k(ret, names, A3)

    _assert_own_contract_data_leads(top, A3_ANSWER)
    assert "Time for Completion for the whole of the Works" in top[0][1]


def test_a5_delay_damages_reaches_the_row_not_the_clause_pointing_at_it(
    two_year_corpus,
):
    """A5 cited Sub-Clause 8.8 and called the rate absent. The clause it had
    only says where the rate is stated; the row that states it must win."""
    ret, names = two_year_corpus
    top = _top_k(ret, names, A5)

    _assert_own_contract_data_leads(top, A5_ANSWER)
    assert "Delay Damages for the whole of the Works" in top[0][1]
    assert DD22_DELAY_CLAUSE not in _blob(top), (
        "the clause that only points at the rate is still in the excerpts:\n"
        + _report(top)
    )


def test_a9_engineer_reaches_the_named_party_row_not_another_years_glossary(
    two_year_corpus,
):
    """A9 was FAIL_WRONG_CONTRACT: another year's Conditions of Contract, and
    only the generic defined term. The filled row names the party."""
    ret, names = two_year_corpus
    top = _top_k(ret, names, A9)

    _assert_own_contract_data_leads(top, A9_ANSWER)
    assert "means the person appointed by the Employer" not in _blob(top), (
        "the defined-term entry is still standing in for the appointment:\n"
        + _report(top)
    )


@pytest.mark.parametrize(
    "ask, expected",
    [
        (A2, "SAR 9,936,000.00"),
        (A6, "365 days"),
    ],
)
def test_the_two_asks_that_already_passed_still_pass(two_year_corpus, ask, expected):
    """A2 and A6 passed live by winning the same race. They are the control:
    a fix that only reorders the pool would take one of them out."""
    ret, names = two_year_corpus
    _assert_own_contract_data_leads(_top_k(ret, names, ask), expected)


@pytest.mark.parametrize("ask", [A2, A3, A5, A6, A9])
def test_no_contract_data_answer_mixes_contract_years(two_year_corpus, ask):
    """#443's property, unchanged: one answer, one PREFIX-YEAR-SEQ. The fix
    elects a different winner — it does not stop electing one."""
    from app.core.rag.retriever import extract_contract_doc_ids

    ret, names = two_year_corpus
    ids = set()
    for name, _text in _top_k(ret, names, ask):
        ids.update(extract_contract_doc_ids(name))
    assert len(ids) <= 1, f"{ask!r} mixed contract ids: {sorted(ids)}"


@pytest.mark.parametrize("ask", [A3, A5, A9])
def test_the_curated_fidic_note_never_outranks_the_contracts_own_row(
    two_year_corpus, ask,
):
    """F-KB-1 (#468) was the knowledge base ANSWERING a question the corpus
    could answer better. It may stay in the pool as disclosed background —
    #468's ``class=knowledge_base`` marker is what stops it being quoted as
    the contract, and capping general-knowledge slots is that PR's territory.
    What it may not do is outrank the row that holds the answer.
    """
    ret, names = two_year_corpus
    top = _top_k(ret, names, ask)
    positions = [i for i, (name, _t) in enumerate(top) if name == KB_NAME]

    assert 0 not in positions, (
        "the FIDIC knowledge base is answering for the contract:\n"
        + _report(top)
    )
    own = [i for i, (name, _t) in enumerate(top) if name == DD23_NAME]
    assert own and (not positions or min(own) < min(positions)), (
        "the knowledge base outranked the project's own Contract Data:\n"
        + _report(top)
    )


# ── mechanism 1: a cross-reference is not the row ─────────────────────────


def test_a_clause_that_only_cross_references_the_contract_data_is_not_a_row():
    """"...at the rate stated in the Contract Data..." tells the reader where
    to look. It must not be scored as though it were the Contract Data."""
    assert contract_data_mention_is_only_a_cross_reference(DD22_DELAY_CLAUSE)
    assert contract_data_mention_is_only_a_cross_reference(DD22_TFC_CLAUSE)
    assert contract_data_mention_is_only_a_cross_reference(
        DD22_ENGINEER_DEFINITION
    )
    assert contract_data_particulars_delta(DD22_DELAY_CLAUSE) <= 0.0, (
        "a pointer to the rate still collects the Contract Data heading tier"
    )
    assert contract_data_particulars_delta(DD22_TFC_CLAUSE) <= 0.0


#: The discriminator is the delicate part of this change, so it is pinned as
#: a table rather than by example. True means "a pointer to the Contract
#: Data", which earns no heading tier.
_XREF_CASES = [
    # Genuine headings and sections — the tier exists for these.
    ("Contract Data\n1.1.75 Time for Completion | 640 days", False),
    ("PARTICULAR CONDITIONS PART A - CONTRACT DATA\n\n1.1.1 ACA | SAR 900,000.00",
     False),
    ("Appendix to Tender\nItem 3 Performance Bond ... 10%", False),
    ("Section 4 — Contract Particulars\n4.1 Retention | 5%", False),
    ("Table 2: Contract Data summary — 1.1.75 | 640 days", False),
    # Subject position, not a reference: this sentence STATES the particular.
    ("The Contract Data states 640 days for the whole of the Works.", False),
    # Pointers — a clause telling the reader where the value is stated.
    ("...at the rate stated in the Contract Data for every calendar day.", True),
    ("...as stated in the Contract Data, being 1,095 days.", True),
    ("...set out in the Contract Data at 0.1% per day.", True),
    ("...named in the Contract Data or replaced under 3.6 within 28 days.", True),
    ("...specified in the Contract Data as 365 days.", True),
    ("...the period given in the Contract Data (10% cap).", True),
    ("...the amount inserted in the Contract Data, SAR 8,640,000.00.", True),
    ("...listed in the Appendix to Tender at 5%.", True),
    ("...referred to in the Contract Particulars as 20 days.", True),
    ("...under the Contract Data the notification period is 365 days.", True),
    ("...per the Contract Data, 640 days.", True),
]


@pytest.mark.parametrize("text, is_pointer", _XREF_CASES)
def test_heading_versus_cross_reference_discriminator(text, is_pointer):
    assert contract_data_mention_is_only_a_cross_reference(text) is is_pointer
    if is_pointer:
        assert contract_data_particulars_delta(text) <= 0.0
    else:
        assert contract_data_particulars_delta(text) > 0.0


def test_a_chunk_with_no_contract_data_mention_is_not_a_cross_reference():
    """"No mention" and "only a pointer" are different answers. Returning
    True here would demote every chunk in the corpus that never names the
    Contract Data at all."""
    assert not contract_data_mention_is_only_a_cross_reference(
        "Sub-Clause 4.1. The Contractor shall design the Works."
    )
    assert not contract_data_mention_is_only_a_cross_reference("")


def test_a_real_contract_data_heading_still_earns_the_heading_tier():
    """The tier exists for a Contract Data section that reached retrieval
    through the plain word chunker, with no index-time prefix. A heading
    starts a line; a cross-reference is governed by a preposition."""
    plain_section = (
        "Particular Conditions Part A - Contract Data\n"
        "1.1.75 Time for Completion for the whole of the Works | 640 days\n"
        "8.8 Delay Damages | 0.1% of the Contract Price per calendar day\n"
    )
    assert not contract_data_mention_is_only_a_cross_reference(plain_section)
    assert contract_data_particulars_delta(plain_section) > 0.0

    # One genuine heading is enough, even alongside a cross-reference.
    mixed = plain_section + (
        "The rate shall be that stated in the Contract Data.\n"
    )
    assert not contract_data_mention_is_only_a_cross_reference(mixed)
    assert contract_data_particulars_delta(mixed) > 0.0


def test_the_indexed_particulars_rows_still_outrank_everything():
    """The index-time prefix keeps the strongest lift. Untouched by the
    cross-reference rule: a prefixed row is decided before it runs."""
    row = _dd23_delay_damages_row()
    assert contract_data_particulars_delta(row) > contract_data_particulars_delta(
        DD22_DELAY_CLAUSE
    )


# ── mechanism 2: a particular is not always a figure ──────────────────────


def test_asking_who_a_contract_role_is_asks_for_a_particular():
    """A9's ask never entered the particulars path, so nothing ran on it."""
    assert query_asks_for_contract_particulars(A9)
    assert query_asks_for_contract_particulars("Who is the Employer?")
    assert query_asks_for_contract_particulars(
        "What is the name of the Engineer's Representative?"
    )


def test_a_definition_question_about_the_same_role_is_still_a_definition():
    """The glossary path must keep the questions it is right for."""
    assert not query_asks_for_contract_particulars(
        "What does Engineer mean under the contract?"
    )
    assert not query_asks_for_contract_particulars(
        "Give me the definition of the Employer's Representative"
    )


def test_an_unrelated_who_question_is_not_pulled_onto_the_particulars_path():
    """D1 asks who signed a letter. It is not a Contract Data lookup."""
    assert not query_asks_for_contract_particulars(
        "Who signed the letter about the batching plant at Creek Bend, "
        "and in what capacity?"
    )
    assert not query_asks_for_contract_particulars(
        "What is the document number and revision of the priced Bill of "
        "Quantities, and who prepared it?"
    )


def test_a9_is_the_only_battery_ask_this_reclassifies():
    """Containment. Measured against `main` across all 35 frozen asks in
    ``tests/fixtures/ui_phys/questions.json``: A9 is the single case whose
    classification moves. These are the ones it must not move — each would
    change which pipeline answers a question that is already passing.
    """
    import json
    from pathlib import Path

    catalog = json.loads(
        (
            Path(__file__).resolve().parent
            / "fixtures" / "ui_phys" / "questions.json"
        ).read_text(encoding="utf-8")
    )
    cases = catalog["cases"]
    must_not = ["A8", "B1", "B6", "C2", "C4", "D1", "D3", "E3", "E4", "G1", "G5", "G6"]
    for cid in must_not:
        ask = cases[cid]["ask"]
        assert not query_asks_for_contract_particulars(ask), f"{cid}: {ask!r}"
    for cid in ("A1", "A3", "A5", "A6", "A9"):
        ask = cases[cid]["ask"]
        assert query_asks_for_contract_particulars(ask), f"{cid}: {ask!r}"


def test_a_particulars_row_whose_value_is_a_name_counts_as_filled():
    """A Notices block carries parties and methods, not figures. It is still
    filled in, and it is the only place A9's answer exists."""
    notices_only = (
        "CONTRACT DATA particulars — filled-in amount / duration / "
        f"percentage [{DD23_NAME}].\n"
        "Contract Data\n"
        "Approved method of electronic communication: ProjectMail\n"
        "Engineer: Northwater Engineers (Demo Saudi Limited)"
    )
    from app.core.rag.retriever import _CD_FILLED_VALUE_RE

    assert not _CD_FILLED_VALUE_RE.search(notices_only), (
        "the fixture must carry no figure at all, or it proves nothing"
    )
    assert is_contract_data_particulars_row(notices_only)
    assert contract_data_particulars_delta(notices_only) > 0.0


def test_a_row_keeps_the_documents_own_separator_when_the_parser_could_not_split():
    """`_line_packed_chunks` is the fallback for a Contract Data section the
    row parser could not split — it keeps the raw lines, and therefore the
    document's own ``key | value`` or dot-leader separator. Those rows carry
    values too, so the same predicate has to see them."""
    pipe_rows = (
        "CONTRACT DATA particulars — filled-in amount / duration / "
        f"percentage [{DD23_NAME}].\n"
        "Contract Data\n"
        "\n"
        "Engineer | Northwater Engineers\n"
        "Employer's address ......... 14 Spur Road"
    )
    assert is_contract_data_particulars_row(pipe_rows)

    keys_only = (
        "CONTRACT DATA particulars — filled-in amount / duration / "
        f"percentage [{DD23_NAME}].\n"
        "Contract Data\n"
        "Engineer |\n"
        "Employer's address .........   "
    )
    assert not is_contract_data_particulars_row(keys_only)


def test_an_empty_particulars_window_is_not_answer_bearing():
    """A window of unfilled keys must not win a pool. Its rows have no value."""
    unfilled = (
        "CONTRACT DATA particulars — filled-in amount / duration / "
        f"percentage [{DD23_NAME}].\n"
        "Particular Conditions Part A - Contract Data\n"
        "1.1.67 Sections\n"
        "4.3.7 Parent Company Guarantee"
    )
    assert not is_contract_data_particulars_row(unfilled)


def test_the_defined_term_glossary_is_never_answer_bearing():
    """Widening "filled" to accept a name must not let prose in."""
    glossary = (
        '"Accepted Contract Amount" means the amount accepted in the Letter '
        "of Acceptance for the execution and completion of the Works."
    )
    assert not is_contract_data_particulars_row(glossary)
    assert contract_data_particulars_delta(glossary) < 0.0


# ── mechanism 3: the election ─────────────────────────────────────────────


def _ranked(*pairs):
    return list(pairs)


def test_the_election_picks_the_contract_owning_the_filled_row():
    """Rank 1 is another year's pointer; the row that answers is below it."""
    row = _dd23_delay_damages_row()
    elected = elect_answer_bearing_contract(
        A5, _ranked((DD22_NAME, DD22_DELAY_CLAUSE), (DD23_NAME, row)),
    )
    assert elected == "dd-2023-118"


def test_the_election_declines_when_no_filled_row_is_in_the_pool():
    """Nothing answer-bearing to elect on — arrival order still decides, so
    this cannot change the ranking of a corpus without Contract Data."""
    assert elect_answer_bearing_contract(
        A5, _ranked((DD22_NAME, DD22_DELAY_CLAUSE)),
    ) is None


def test_the_election_declines_for_a_question_that_wants_no_particular():
    """A drawing or method-statement lookup must not have its pool decided by
    whichever Contract Data row happens to be in it."""
    row = contract_data_particulars_chunks(
        DD23_CONTRACT_DATA, filename=DD23_NAME,
    )[0]
    assert elect_answer_bearing_contract(
        "Summarize drawing IP-INF-054-0000-JCB-DWG-LI-200-0001056-04",
        _ranked((DD23_NAME, row)),
    ) is None
    assert elect_answer_bearing_contract(
        "What does Accepted Contract Amount mean?", _ranked((DD23_NAME, row)),
    ) is None


def test_a_named_contract_question_never_reaches_the_election(two_year_corpus):
    """#443 owns the named path and is untouched. A question naming the OTHER
    year must still fail closed rather than be rescued by an elected row."""
    ret, names = two_year_corpus
    chunks, _ = ret.retrieve_with_filter(
        "Per DD-2024-999, what are the Delay Damages for the whole of the "
        "Works?",
        PROJECT,
        k=5,
    )
    assert chunks == [], [names.get(c.doc_id, "") for c in chunks]

    chunks, _ = ret.retrieve_with_filter(
        "Per the DD-2022-175 contract, what are the Delay Damages for the "
        "whole of the Works?",
        PROJECT,
        k=5,
    )
    assert chunks, "the named year is in the fixture"
    assert all(names.get(c.doc_id, "") == DD22_NAME for c in chunks), (
        "the election overrode an explicitly named contract"
    )


# ── mutation probes ───────────────────────────────────────────────────────


def test_the_unfilled_wrong_year_window_outscores_the_filled_row(
    two_year_corpus,
):
    """The probe below is only meaningful if the decoy genuinely leads on
    score, so measure it rather than assume it. Prefix bonus plus the full
    label bonus, on the higher raw cosine, puts the wrong year's empty table
    ahead of the row that states the answer."""
    row = _dd23_delay_damages_row()
    assert contract_data_particulars_delta(
        DD22_UNFILLED_CONTRACT_DATA
    ) == contract_data_particulars_delta(row), (
        "the decoy no longer shares the family bonus — it proves nothing"
    )
    assert not is_contract_data_particulars_row(DD22_UNFILLED_CONTRACT_DATA)
    assert is_contract_data_particulars_row(row)


@pytest.mark.parametrize(
    "ask, answer", [(A3, A3_ANSWER), (A5, A5_ANSWER), (A9, A9_ANSWER)],
)
def test_mutation_probe_arrival_order_election_brings_the_failure_back(
    two_year_corpus, monkeypatch, ask, answer,
):
    """Restore arrival-order election and the live failure returns: the top-5
    is entirely the wrong contract year and the answer is at no rank.

    This is what makes the three tests above evidence rather than decoration
    — the fixture genuinely reproduces the defect it claims to, and it shows
    the guarantee cannot be bought by scoring alone. A boost is bounded; the
    number of chunks the other contract can put at rank 1 is not.
    """
    ret, names = two_year_corpus
    monkeypatch.setattr(
        ret, "elect_answer_bearing_contract", lambda _q, _docs: None,
    )
    top = _top_k(ret, names, ask)

    assert top, "the probe must reproduce a wrong answer, not an empty one"
    assert DD22_NAME == top[0][0], (
        "probe did not put the wrong year at rank 1:\n" + _report(top)
    )
    assert DD23_NAME not in [name for name, _t in top], (
        "probe did not reproduce the lock — the contract being asked about "
        "is still in the top-5:\n" + _report(top)
    )
    assert answer not in _blob(top)


def test_mutation_probe_the_cross_reference_rule_is_what_demotes_the_clause(
    monkeypatch,
):
    """Disable the cross-reference rule and Sub-Clause 8.8 is scored as a
    Contract Data row again — the misfire behind A5's citation."""
    from app.core.rag import retriever as ret

    assert contract_data_particulars_delta(DD22_DELAY_CLAUSE) <= 0.0
    monkeypatch.setattr(
        ret, "contract_data_mention_is_only_a_cross_reference", lambda _t: False,
    )
    assert ret.contract_data_particulars_delta(DD22_DELAY_CLAUSE) > 0.0


def test_mutation_probe_a9_needs_the_role_ask_to_be_recognised(
    two_year_corpus, monkeypatch,
):
    """With the role ask unrecognised, nothing in the particulars machinery
    runs on A9 and another year's defined term answers it."""
    ret, names = two_year_corpus
    real = ret.query_asks_for_contract_particulars
    monkeypatch.setattr(
        ret,
        "query_asks_for_contract_particulars",
        lambda q: False if q == A9 else real(q),
    )
    top = _top_k(ret, names, A9)

    assert top
    assert A9_ANSWER not in _blob(top), (
        "probe did not reproduce A9 — the role ask is not load-bearing:\n"
        + _report(top)
    )
