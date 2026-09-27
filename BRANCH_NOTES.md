# BRANCH_NOTES — `agent-e/fw4-spec-retrieval`

FIX WAVE 4, #2 RETRIEVAL. Local lane for Agent D's batch PR. No pull request from this branch. No merge, no deploy, no AWS / Render / env / Neon changes. Not READY.

Base: `b13aed07b570ca6c82d04a8315052e726a9a8d60` (main).

## Root cause

Pre-answer retrieval is `rag_inject` → `build_retrieval_query` → `retrieve_with_filter` (`RAG_K` 5) → token cap → system message. `build_retrieval_query` returns S1/S2 unchanged (they are not thin follow-ups), and `RAG_K` was not the limit. The misses are in `retrieve_with_filter`:

1. **Vol 2 Specification (4 of 9) cl 3.1.25.8 is never a candidate.** It says spacers give "the cover specified herein, on the Drawings or as directed" and states no cover millimetre. `_fetch_numeric_requirement_chunks` only admits chunks that state a cover length, and the vector leg does not pull a tie-wire / spacer paragraph for "minimum concrete cover for foundations cast directly against soil". Local rag_inject over ~780 evidence chunks with bge-small: not in the 79-chunk pool. Had it been pooled, `_cap_specification_class_bonus` would still have cut its +1.2 class lift, because an unrelated "1.29mm" tie-wire gauge sits in the same chunk.
2. **ST-200-0000007-04 is pooled but never lifted.** Its 100 mm is a list item ~120 characters after its "CLEAR CONCRETE COVER TO STEEL REINFORCEMENT SHALL NOT BE LESS THAN THE FOLLOWING:" heading, past the 64-character window in `_mm_near_cover_phrase`. It stays at cosine (~0.74, pool rank 33) while chunks with a millimetre next to "cover" get +2.5.

All five slots then go to Vol 5 (4 of 5) chunk 92, Vol 5 (2 of 5) chunk 79, ST-400 notes and a Vol 2 (3 of 9) earth-well lid "concrete cover of 250mm x 250mm x 10mm". That matches the FW3 remeasure (chunk 92 6/6, chunk 79 5/6, ST-400, spec 0/6, ST-200 0/6).

## Change (`app/core/rag/retriever.py`)

Flag `RETRIEVAL_SPEC_DEFERRAL`, default **on**. `=0` restores b13aed07 exactly (tested).

- `chunk_states_cover_length` → `_cover_clause_states_length`: a cover phrase (nominal / concrete / clear / minimum cover, cover to (steel) reinforcement) states a length when a single millimetre is within 64 characters, or is a list item up to 160 characters after the phrase with no sentence end or new numbered note in between. A millimetre that is one side of a size (`250mm x 250mm`) is not a cover length.
- `chunk_defers_cover_to_drawings`: one sentence names the concrete/reinforcement cover and sends it to the drawings ("on the Drawings", "as shown on the drawings", "per drawings"). A manhole-cover sentence does not count.
- `_rescue_spec_deferral_chunks` (candidate pool, before the top-k cut): only for a specification-scoped cover ask (`query_asks_spec_deferred_cover`), and only when no specification deferral clause is already pooled. `chunks_containing_all` with `drawings` + a cover phrasing, scoped to specification volumes already in the pool, plus an open pass on "cover specified" / "specified cover". Admits specification-named chunks whose sentence defers. The clause enters with its own cosine to the query.
- `_apply_spec_deferral_boost` (after the numeric boost): the deferral clause takes the stated-figure lift (+2.5) and keeps its class lift (`_cap_specification_class_bonus` skips it). With it in the pool, drawing-named chunks that state the cover get +0.5 (the authority the clause names) and cover chunks naming the asked element (foundation / footing / raft …) get +0.3.
- Compaction asks never take this path. S2 injection is byte-identical flag on vs off (tested).

## Tests

`tests/test_fw4_spec_deferral_retrieval.py`, fixture `tests/fixtures/fw4_spec_deferral_chunks.json` (28 chunk bodies from the FW3 evidence files: the two targets, chunk 92 ×2 + 518, chunk 79 ×2, ST-400 462/463/469/477, Vol 3 note 5.5 and the ST-200 note copies, Vol 2 spec distractors incl. the earth-well lid, and the S2 set: RSM 15492 chunks 8/9, Vol 5 2/5 chunks 81/82, ITP, CCF, TM-400). Drive paths replaced by `FIXTURE-fw4` names. Each end-to-end test runs `rag_inject` with the fake embedder and with BAAI/bge-small-en-v1.5 (skipped unless cached and `HF_HUB_OFFLINE=1`).

Injected ranks, fixture corpus (`rag_inject`, k=5):

| ask | embedder | b13aed07 | this branch |
|---|---|---|---|
| S1 3.1.25.8 | bge | — | 1 |
| S1 ST-200-0000007-04 | bge | — | 3 |
| S1 3.1.25.8 | fake | — | 1 |
| S1 ST-200-0000007-04 | fake | — | 4 |
| S2 RSM 15492 chunk 9 | bge | — | — (same clause via Vol 5 2/5 chunks 81/82 at 4, 5 both) |
| S2 RSM 15492 chunk 9 | fake | 1 | 1 |

On b13aed07 the new file fails 6 (both S1 end-to-end params, 4 detector units) and passes 7. On this branch: 13 passed (with `tests/test_fw3_s1_retrieval_rank.py`: 19 passed).

Full suite (`pytest tests/ --ignore=tests/browser`, SQLite default, `RAG_EMBEDDING_MODEL=fake`, `HF_HUB_OFFLINE=1`): `CEREBRUM_VIRGIN=false` + `CEREBRUM_DOMAIN_KITS=construction` 8231 passed, 33 skipped, 1 xfailed, 1 xpassed; `CEREBRUM_VIRGIN=true` 7982 passed, 282 skipped, 1 xfailed, 1 xpassed. `scripts/scan_exception_pass.py`: `RETURN: 0 new`.

## Limits

Fixture store and a ~780-chunk local evidence corpus, not the 123,233-chunk prod index; prod was not queried. In the larger local corpus S1 injects 3.1.25.8 at 1 and ST-200-0000007-04 at 3; the other slots go to copies of the same 100 mm drawing note (Vol 3 Drawings 6/7 and 4/7), so chunk 92 and the ST-400 75 mm notes drop out there. S2 chunk 9 is not injected with bge before or after; the same 95% MDD / CBR 25 clause is, and this change does not touch that path.
