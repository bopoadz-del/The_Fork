# BRANCH_NOTES — `agent-e/fw4-spec-retrieval`

FIX WAVE 4, #2 RETRIEVAL. Local lane for Agent D's batch PR. No pull request from this branch. No merge, no deploy, no AWS / Render / env / Neon changes. Not READY.

Base: `b13aed07b570ca6c82d04a8315052e726a9a8d60` (main). Merged `origin/main` at `4c4efc8` (PR #716 Dockerfile change) after the patch.

## Root cause

Pre-answer retrieval is `rag_inject` → `build_retrieval_query` → `retrieve_with_filter` (`RAG_K` 5) → token cap → system message. `build_retrieval_query` returns S1/S2 unchanged (they are not thin follow-ups), and `RAG_K` was not the limit. The misses are in `retrieve_with_filter`:

1. **Specification clause 3.1.25.8 is never a candidate.** It says spacers give the cover specified in the clause, on the drawings, or as directed, and states no cover millimetre. `_fetch_numeric_requirement_chunks` only admits chunks that state a cover length, and the vector leg does not pull a spacer paragraph for "minimum concrete cover for foundations cast directly against soil". Had it been pooled, `_cap_specification_class_bonus` would still have cut its +1.2 class lift, because an unrelated wire-gauge millimetre sits in the same chunk.
2. **The footing-cover drawing note is pooled but never lifted.** Its 100 mm is a list item about 120 characters after the cover heading, past the 64-character window in `_mm_near_cover_phrase`. Chunks with a millimetre next to "cover" get +2.5 and take the slots.

## Change (`app/core/rag/retriever.py`)

Flag `RETRIEVAL_SPEC_DEFERRAL`, default **on**. `=0` restores b13aed07 exactly (tested).

- `chunk_states_cover_length` → `_cover_clause_states_length`: a cover phrase (nominal / concrete / clear / minimum cover, cover to (steel) reinforcement) states a length when a single millimetre is within 64 characters, or is a list item up to 160 characters after the phrase with no sentence end or new numbered note in between. A millimetre that is one side of a size (`250mm x 250mm`) is not a cover length.
- `chunk_defers_cover_to_drawings`: one sentence names the concrete/reinforcement cover and sends it to the drawings ("on the Drawings", "as shown on the drawings", "per drawings"). A hatch-cover sentence does not count.
- `_rescue_spec_deferral_chunks` (candidate pool, before the top-k cut): only for a specification-scoped cover ask (`query_asks_spec_deferred_cover`), and only when no specification deferral clause is already pooled. `chunks_containing_all` with `drawings` + a cover phrasing, scoped to specification volumes already in the pool, plus an open pass on "cover specified" / "specified cover". Admits specification-named chunks whose sentence defers. The clause enters with its own cosine to the query.
- `_apply_spec_deferral_boost` (after the numeric boost): the deferral clause takes the stated-figure lift (+2.5) and keeps its class lift (`_cap_specification_class_bonus` skips it). With it in the pool, drawing-named chunks that state the cover get +0.5 (the authority the clause names) and cover chunks naming the asked element (foundation / footing / raft …) get +0.3.
- Compaction asks never take this path. S2 injection is byte-identical flag on vs off (tested).

## Tests

`tests/test_fw4_spec_deferral_retrieval.py`, fixture `tests/fixtures/fw4_spec_deferral_chunks.json`. Chunk bodies are synthetic (`FIXTURE-e-20260927-…`, invented project Example Harbour Works). They keep the properties the tests need: clause 3.1.25.8 deferring cover with no cover millimetre; a drawing note whose 100 mm footing figure sits about 120 characters after the cover phrase; 75 mm contact-with-soil drawing notes; a lid size `250mm x 250mm`; and 95% MDD / CBR 25 compaction clauses. Each end-to-end test runs `rag_inject` with the fake embedder and with BAAI/bge-small-en-v1.5 (skipped unless cached and `HF_HUB_OFFLINE=1`).

On the synthetic corpus the tests assert: S1 injects the deferral clause and the footing-cover drawing; `RETRIEVAL_SPEC_DEFERRAL=0` injects neither; S2 still injects the 95% MDD / CBR 25 clause and does not inject those two cover docs; S2's injected doc list is identical with the flag on and off.

### G3 rephrasings (FW5)

`test_g3_*`: three other S1 wordings (per / according to / under the spec, trailing or leading) go from both chunks absent at k=5 with `=0` to both injected with the default, fake and bge. A fourth, "What cover does the spec require for footings poured against earth?", first missed with the flag on: the spec was the grammatical subject, not "per the spec", and the ask said bare "cover". Repair (general, deferral path only): `query_names_specification` also accepts "the spec requires/says/states/specifies…" and "in/by/from the spec"; `query_asks_concrete_cover` accepts bare "cover" next to a concrete element word, excluding lids, hatches, cover letters and "cover" as a verb. The global class lift and `asked_quantity_kinds` are unchanged. For that wording the footing drawing is already injected without the flag, so only the clause goes from absent to ranked. Four S2 wordings: the injected list is identical with the flag on and off, and the 95% MDD clause stays in.

## Limits

Fixture store only. Prod was not queried.
