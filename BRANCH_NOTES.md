# BRANCH_NOTES — `agent-e/s1-s2-s4-first-line-hard-rule`

Local lane for Agent D to fold. No pull request, no merge, no deploy.

Base: `209bc8363df8168f9feacc88cebc28a0752b9942` (tip `209bc83`).

## Corpus truth

S2 compaction is **PRESENT**. The numeric figure is in Vol 5 Other Documents
(geotech RSM), not in the usual Vol 2 Specification sentence: sub-grade
compacted to **95% of maximum dry density** for a minimum CBR of 25, tied to
pavement / subgrade design. Vol 2 often only says "properly compacted". That
qualitative sentence is what live SET5 S2 first lines quoted. Do not rewrite
S2 as absent, and do not treat a formatting change as the fix.

S1 minimum cover (75 mm) and S4 governing contract(s) are present as well
(close-out SPLITs).

## Tests added

`tests/test_s1_s2_s4_first_line_hard_rule.py` — synthetic filenames and
quotes only. No client documents.

They failed on the unchanged post-check (13 failures): the Hard-rule text
did not yet require the numeric figure and the document together, and
`_postprocess_answer` left these first lines in place:

- S1: `75 mm` with no document, or the document with no `75 mm`, or a
  narrative opener.
- S2: specification name + "properly compacted", with `95%` sitting in the
  injected Vol-5-style excerpt.
- S4: a governing-law opener that never names the visible contract; two
  contracts were answered by naming one.

A first line that only says "properly compacted" plus the specification name
fails the S2 test. The same test passes once the first line carries `95%`
and the document that states it.

## What changed

1. Hard-rule wording in `app/agents/configs/project-assistant.md` and
   `app/agents/configs/heavy-reasoning.md` (same two bullets in each).
2. Deterministic guard `app/agents/first_line_hard_rule.py`, called at the
   end of `_postprocess_answer` in `app/agents/runtime.py`.
   Kill-switch: `FIRST_LINE_HARD_RULE=0`.

The guard reads the excerpts already on the turn. It does not invent a
percent: excerpts that only say "properly compacted" are left alone. When
the question names a source class and that class's own document states a
percent, that percent is the first-line figure (specification 98% stays
ahead of another file's 95%). When the number is in a different document,
the first line names that file and says it is not the specification.
"Which contract governs this project?" names the one non-template contract,
or asks which contract is meant when more than one is visible. A template
filename is not a second contract.

## Hard-rule text, before → after

Governing-source bullet, before:

> Open with the figure and the document that carries it -- "Specification Section 03 30 00 gives 75 mm" -- not with a paragraph that reaches the citation at the end.

Governing-source bullet, after (the new obligation):

> The FIRST line must contain BOTH the numeric figure (with its unit or percent) AND the document name that carries it -- "Specification Section 03 30 00 gives 75 mm" -- not a paragraph that reaches the citation at the end, and not a figure whose document appears only in a later sentence. A qualitative clause ("properly compacted", "as required") is not the figure when the retrieved context also states a number (for example 95% of maximum dry density). Lead with that number and the filename of the document that states it, in the FIRST line, even when that document is a different volume from the one the question named.

Contract bullet, before:

> When the question says only "the contract", "the project" or "the specification", name the one you used -- its number and title -- in the FIRST line of the answer, and cite it. When the choice would change the answer and nothing in the question or the conversation picks one, ask which is meant instead of choosing silently.

Contract bullet, after (sentence added):

> "Which contract governs this project?" is the same duty: the FIRST line names that contract's number and title when one contract is visible in the retrieved context, and when the retrieved context names more than one contract the FIRST line asks which is meant.

## How D should fold

Fold this branch into the one batch PR. Do not open a second PR and do not
deploy from this branch.

Retrieval follow-up, out of scope here: live S2 still tends to surface Vol 2
Specification ("properly compacted") and not the Vol 5 geotech passage that
holds 95% MDD. This guard will not write 95% unless that passage is in the
turn's excerpts. If a later close-out is still S2 0/6, the next change is
retrieval of the numeric compaction passage, not another prompt edit.

## Local proof

- `CEREBRUM_VIRGIN=false` + `CEREBRUM_DOMAIN_KITS=construction`: the new
  file, name-the-contract, named-source ranking, coverage honesty, and
  citation provenance — 100 passed.
- `CEREBRUM_VIRGIN=true`: the new file, name-the-contract, named-source
  ranking, and coverage honesty — 56 passed; citation provenance — 44 passed.
- `scripts/scan_exception_pass.py`: no silent `except Exception: pass`;
  `RETURN: 0 new` (76 baselined).

---

# agent-e/set5-s1s2-retrieval

Base: `7c0b25580a6fcd8263f80548b339b09462bff3ff`.
No PR. No force-push, no rebase. No Render, Neon, or env changes. No calls to theshovel.ai.

The notes above are the first-line hard-rule lane already on this base. This section is the retrieval follow-up that lane left open.

## Commits

- Tests: `3e432589d1e0f324a2081b4e3811b2aaa396f2e5` — failing regressions (S1 and S2 absent; foundation-backfill figure still present).
- Fix: `9169702fbf2e0c48ad41f613a41d51f3e60f289c` — kill-switch, subject filter. This notes restoration is a follow-up commit so the earlier lane's notes stay intact.

## Flag

`RETRIEVAL_NUMERIC_REQUIREMENT_BOOST` defaults ON.
`0` / `false` / `no` / `off` skips the supplementary lexical fetch and the score lift, which is the previous ranking.

## Root cause

Both questions say "per the project specification". `source_class_named_by` therefore treats the governing class as specification, and `source_class_adjustment` adds `+1.2` to every filename that says specification. Vol 5 Other Documents get no lift. Fake-embedder cosine on this fixture sits around `0.15`, so any specification chunk in the pool outranks an Other Documents chunk.

That only matters for chunks that enter the pool. The hybrid leg keeps 50 BM25 hits (`HYBRID_FETCH_PER_LEG`). FTS5 does not stem.

S1. Query: minimum concrete cover to reinforcement for foundations.
The durability chunk says "nominal cover" and a millimetre length for foundations cast against blinding or soil. Vol 2 neighbours repeat "specified minimum concrete cover to reinforcement" and state no length. On this fixture that chunk's BM25 rank is **57**, outside the lexical 50, so it never reaches the re-rank. The retrieved five are all Vol 2 Specification pads (scores about `1.34`–`1.37`, which is cosine plus the `+1.2` filename lift).

S2. Query: compaction required under road pavement.
The geotech chunk says "sub-grade" / "embankment", a percent of maximum dry density written in words and digits, and a CBR value. It does not say "road pavement", and "compacted" is not the token "compaction". BM25 rank on this fixture is **116** (essentially unmatched). The retrieved five are again Vol 2 Specification pads that say "properly compacted" or "minimum concrete cover", with no percent of maximum dry density.

## Fix

Generic, in `app/core/rag/retriever.py`. No answer figures, document names, or chunk ids in product code.

1. When the question asks for concrete cover or for compaction, a supplementary BM25 query appends that quantity's vocabulary (nominal cover / millimetre / blinding, or compacted / sub-grade / embankment / maximum dry density / CBR). Only a chunk that states a number in the asked unit is admitted, at score `0` so a BM25 rank is never read as a cosine.
2. Those chunks then receive `+2.5` (`-2.5` on the lexical-only path, where a better BM25 rank is more negative). A specification chunk that states its own number keeps the `+1.2` filename lift on top of this, so it still leads for its own question. A qualitative "properly compacted" or "short cover" clause gets nothing.
3. Compaction is split by element. A road / pavement question only lifts a chunk that also says pavement, road, carriageway, sub-grade, or embankment. A backfill / foundation question only lifts a chunk that says backfill or foundation. The foundation-backfill specification clause is not pulled into the road-pavement set, and the sub-grade clause is not lifted on the backfill question.

## Files

- `tests/test_s1_s2_numeric_requirement_retrieval.py` — fixture ids and names prefixed `FIXTURE-e-20260925-`. Real `retrieve_with_filter` on a SQLite FTS5 store. Ranking is not mocked.
- `app/core/rag/retriever.py` — the flag, the expansion, the admit, and the lift. Both the hybrid path and the lexical-only path.

## Tests

`scripts/scan_exception_pass.py`: exit 0. `NO silent except Exception: pass handlers.` `RETURN: 0 new (76 baselined).`

Same file list, `RAG_EMBEDDING_MODEL=fake`:

- `CEREBRUM_VIRGIN=false` `CEREBRUM_DOMAIN_KITS=construction`: **403 passed, 4 skipped, 0 failed** (22s).
- `CEREBRUM_VIRGIN=true`: **403 passed, 4 skipped, 0 failed** (22s).

Included: `tests/test_s1_s2_numeric_requirement_retrieval.py` (6), `tests/test_s1_s2_s4_first_line_hard_rule.py` (first-line hard rule and the no-invention case), `tests/test_named_standard_attribution.py` (P4b / P3b / P6b), `tests/test_e6_follow_up_keeps_stated_total.py`, `tests/test_set3_remaining_e1_e3_e6_f1.py`, `tests/test_attached_documents.py` (S4), `tests/test_the_named_source_governs.py` (foundation-backfill source class), and the retrieval modules (`test_*retriev*`, hybrid, dual-query, contract-data, spec-title, letter-filename, layered rerank, identifier, schedule register, candidate over-fetch).

New file alone, before the fix: **2 failed, 1 passed** (S1 absent, S2 absent, backfill figure still present). After the fix: **6 passed**.

## Before / after

Fixture corpus, `retrieve_with_filter(..., k=5)`, fake embedder. `doc`, `chunk_id`, `score`. TARGET is the chunk the question needs.

### BEFORE (`RETRIEVAL_NUMERIC_REQUIREMENT_BOOST=0`)

S1 — target absent:

1. doc=`FIXTURE-e-20260925-vol2-specification-pad-48` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-48:1` score=`1.368464`
2. doc=`FIXTURE-e-20260925-vol2-specification-pad-25` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-25:1` score=`1.358863`
3. doc=`FIXTURE-e-20260925-vol2-specification-pad-24` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-24:0` score=`1.355483`
4. doc=`FIXTURE-e-20260925-vol2-specification-pad-18` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-18:1` score=`1.342794`
5. doc=`FIXTURE-e-20260925-vol2-specification-pad-17` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-17:1` score=`1.341652`

S2 — target absent:

1. doc=`FIXTURE-e-20260925-vol2-specification-pad-46` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-46:0` score=`1.375903`
2. doc=`FIXTURE-e-20260925-vol2-specification-pad-6` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-6:1` score=`1.349514`
3. doc=`FIXTURE-e-20260925-vol2-specification-pad-40` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-40:0` score=`1.337971`
4. doc=`FIXTURE-e-20260925-vol2-specification-pad-28` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-28:1` score=`1.332593`
5. doc=`FIXTURE-e-20260925-vol2-specification-pad-27` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-27:0` score=`1.327174`

Foundation backfill (must stay): the specification compaction chunk is present at rank 5, score `1.319337`.

### AFTER (`RETRIEVAL_NUMERIC_REQUIREMENT_BOOST=1`, the default)

S1 — durability chunk rank 1:

1. doc=`FIXTURE-e-20260925-vol5-other-documents-4` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol5-other-documents-4:0` score=`2.568224` TARGET
2. doc=`FIXTURE-e-20260925-vol2-specification-pad-48` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-48:1` score=`1.368464`
3. doc=`FIXTURE-e-20260925-vol2-specification-pad-25` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-25:1` score=`1.358863`
4. doc=`FIXTURE-e-20260925-vol2-specification-pad-24` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-24:0` score=`1.355483`
5. doc=`FIXTURE-e-20260925-vol2-specification-pad-18` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-18:1` score=`1.342794`

S2 — sub-grade chunk rank 1:

1. doc=`FIXTURE-e-20260925-vol5-other-documents-2` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol5-other-documents-2:0` score=`2.5` TARGET
2. doc=`FIXTURE-e-20260925-vol2-specification-pad-46` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-46:0` score=`1.375903`
3. doc=`FIXTURE-e-20260925-vol2-specification-pad-6` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-6:1` score=`1.349514`
4. doc=`FIXTURE-e-20260925-vol2-specification-pad-40` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-40:0` score=`1.337971`
5. doc=`FIXTURE-e-20260925-vol2-specification-pad-28` chunk_id=`FIXTURE-e-20260925-project:FIXTURE-e-20260925-vol2-specification-pad-28:1` score=`1.332593`

Foundation backfill after the fix: the specification compaction chunk is rank 1, score `3.819337`. The sub-grade chunk is not in that top 5.

## Caveats

- Evidence is this fixture corpus (55 Vol 2 pads plus the clauses), not a replay against the live Master Corpus index. The live service was not queried.
- Rankings use the fake hash embedder, which is the CI retrieval path. Absolute cosine values will differ with `minishlab/potion-base-8M`; the failure mode that was measured (lexical miss plus a `+1.2` specification-filename lift larger than cosine) does not depend on the hash embedder.
- If a specification chunk itself states a millimetre cover, it still outranks Other Documents, because it receives both lifts. The live Vol 2 excerpts that were returned did not state that length; the fixture matches that.
- A compaction question that names neither pavement/road nor backfill/foundation still lifts every dry-density percent. The two SET5 questions each name an element, and those stay apart.
- Repair attempts used on the retrieval fix: 0. The subject split was added before the fix commit so the road-pavement set would not also promote the foundation-backfill clause.
