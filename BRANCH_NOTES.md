# BRANCH_NOTES — `agent-e/fw3-s1-retrieval`

Local lane for Agent D to fold into the batch PR. No pull request from this branch. No merge, no deploy, no Render/Neon/env changes.

Base: `23a04d0931b588b19352d292c53d7db76ad3c7db`.

## Root cause

Pre-answer retrieval is `rag_inject` → `retrieve_with_filter` (`RAG_K` default 5). The specification filename lift lives in `_apply_source_class_preference` (`app/core/rag/retriever.py`): a question matching "per the project specification" adds `_SOURCE_CLASS_BONUS` (1.2) to every chunk whose filename says specification.

That lift stacks on `_NUMERIC_REQUIREMENT_BONUS` (2.5). The cover detector (`chunk_states_cover_length`) treated any "cover" plus any millimetre anywhere in the chunk as a stated cover length. Specification chunks about bollards, raised floors, and rebar fixing therefore scored cosine + 2.5 + 1.2. With cosine near 0 that is the flat **3.7** plateau in the live probe. The durability sentence (nominal cover 50 mm / 75 mm, no "specification" in the filename) kept cosine + 2.5 (~3.26) and landed at rank 22, outside the five chunks passed to the model.

Signed and unsigned copies of that same body each took a slot. Dedupe runs in the same top-k cut.

`app/agents/first_line_hard_rule.py` was not edited. `SEARCH_ALSO_VERBATIM` / `also_query` is not used.

## Flag

`RETRIEVAL_SPEC_BOOST_GUARD` defaults **on**. `RETRIEVAL_SPEC_BOOST_GUARD=0` restores the uncapped lift, the loose cover detector, the old lexical query, and both duplicate slots.

When the flag is on:

- A cover length is a millimetre within 64 characters of "nominal cover", "concrete cover", or "cover to reinforcement".
- On a cover-length ask, the 1.2 filename lift is capped at 0.25 for a chunk that has a cover-word and a millimetre but does not state that length. A chunk that states the asked figure (including P1a's 98% MDD) keeps the full lift. Compaction asks and non-specification classes are not capped.
- The BM25 leg of pre-answer retrieval appends `nominal cover cast against soil casted against blinding`. The embedded query stays the operator's words.
- Duplicate bodies collapse to the higher-scored copy before the top-k cut.

## Dedupe key

`chunk_copy_key`: strip one leading `[source: …]` line, collapse whitespace, lowercase. Bodies shorter than 80 characters are not collapsed. The kept copy is the higher score (the candidate list is already in rank order).

## Fixture ranks (fake embedder, not the live index)

S1 ask, passed k=5, guard off: five `vol2-specification-*` chunks, scores 3.811, 3.758, 3.736, 3.710, 3.708. Cover copies absent.

S1 ask, guard on: `vol5-other-4-signed` score 2.525 (the 75 mm clause), then four specification fillers that do not state a cover length (scores 1.283–1.196). The unsigned copy is not a second slot.

P1a (98% MDD, modified Proctor) is rank 1 at 3.819 with the guard on and off.

S2 (Vol 5 other documents 2 of 5, RSM 15492-Rev0, 95% MDD / CBR 25) is rank 1 at 2.491 with the guard on and off.

S4 ("Which contract governs this project?") does not name the specification class, so this lift does not run. The PSA chunk 1032 text is not in the repo; it was not fixture-ranked.

## Tests

`tests/test_fw3_s1_retrieval_rank.py`. Fixtures prefixed `FIXTURE-e-20260926-`. Clause text mirrors the quoted sentences only.

Relevant suite (this file, numeric-requirement, named-source, P1a, first-line hard rule, spec-title, hybrid, dual-query, letter-filename, rag injection, source class, one-chunk-per-document): **179 passed, 4 skipped** with `CEREBRUM_VIRGIN=false` and the same **179 passed, 4 skipped** with `CEREBRUM_VIRGIN=true`. `RAG_EMBEDDING_MODEL=fake`. `scripts/scan_exception_pass.py`: `RETURN: 0 new`.

## Caveat

These ranks are the fixture store with the fake embedder. The live index was not queried. Live rank 22 / score 3.264 for chunk 92 is from the attached probe on d708b5d, not a re-measure of this branch.
