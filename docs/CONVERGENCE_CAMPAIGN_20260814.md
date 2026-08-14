# Live-fire convergence campaign — The Fork

**Target system:** The Fork, live deployment at `https://theshovel.ai`
(Render `srv-d9s6l67avr4c73aiujsg`, commit `8089edd`).

**Scope (operator, verbatim):** "the fork, is the system, and all the
construction codes and formulas" / "the whole application from A to Z from
log in".

**End-to-end path under test:**
`auth contract -> create project -> upload document -> index -> retrieve a
SYNTHETIC needle -> execute a construction formula -> export to docx`

**Standing condition during this campaign:** open registration was enabled on
this production instance today, and the instance has no per-project data
isolation (empty projects fall back to the Master Corpus by design). The
campaign therefore runs against a publicly-registerable box holding client
data. Flagged to the operator; awaiting their call on reverting the flag.

**Leg NOT covered:** a successful password login. I do not create accounts or
authenticate with passwords, so login-success is operator-verifiable only and
is recorded as UNTESTED rather than passed (rule 5: a claim the harness cannot
falsify is not tested). The login *rejection* path IS tested, and every
authenticated stage runs over the API-key path, which resolves to the same
`require_user` dependency.

**Real dependencies, no mocks:** Neon Postgres, BAAI/bge-small-en-v1.5 embedder,
Kimi k2.6 (Moonshot), Cloudflare R2, python-docx renderer, Cloudflare DNS +
Render edge.

## Enforcement rules active

| # | Claim the system makes | How the harness falsifies it |
|---|---|---|
| E1 | "document uploaded" | read the chunk count back from the index; >0 required |
| E2 | "answer grounded in YOUR document" | assert the Master-Corpus fallback note is ABSENT and sources name the uploaded file |
| E3 | "answer is correct" | question has a unique needle value present in no other corpus document |
| E4 | "exported" | unzip the docx and read the needle back out of the XML |
| E5 | "persisted" | re-read via a separate request, not the response body of the write |

## Wrap-around greens explicitly refused

- Chat `200` whose answer came from the Master Corpus while the question was about the uploaded doc.
- Upload `201` where indexing produced zero chunks.
- Export `200` returning a well-formed docx with no answer content in it.
- `sources: []` treated as acceptable alongside a confident answer.

## Stop rule

Same failure surviving two full runs -> stop running, switch to isolated
empirical probes against the real component.

---

## Ledger (append-only)

### Run 1 — 2026-08-14 16:50 UTC — VERDICT: FAIL (2 real defects, 1 test defect)

Target `https://theshovel.ai`, commit `8089edd`. Project created for the run:
`60cfc7bf` (`convergence-campaign-20260814-195036`) — **never delete**.

| Stage | Result | Note |
|---|---|---|
| S1a login rejects unknown user | PASS | HTTP 401, no user enumeration |
| S1b unauthenticated read refused | PASS | HTTP 401 on `/v1/agents` |
| S1c api key authenticates | PASS | 14 agents |
| S2 project persisted (independent read-back) | PASS | create 201, separate GET 200 |
| S3 upload actually indexed | **FAIL** | chunk_count 0 |
| S4 synthetic needle retrieved | **FAIL** | "could not confirm this reference in the indexed project sources" |
| S5 calculator executed | pass-but-unsound | see F3 |
| S5b arithmetic correct | PASS | 945 present |

**F1 — MAJOR: uploaded documents are never indexed.**
Mechanism (confirmed in code + `render.yaml:18-23`, not inferred): the arq
ingest worker was deleted 2026-08-08 to halve the compute bill, on the stated
assumption that "inline ingestion in the web request is acceptable". But
`app/routers/upload.py:124-132` only falls back to inline
`doc_index.maybe_eager_index` when `enqueue_ingest()` returns **False**, and
`app/worker/ingest_queue.py:12-21` returns False only when `REDIS_URL` is
unset or Redis errors. Redis was deliberately **kept** (sessions, rate
limiting). So the enqueue always succeeds, the inline fallback never runs,
and no worker drains the queue. Every document uploaded through the app since
the worker was removed sits at `chunk_count = 0` forever.
Why it escaped notice: the Master Corpus is loaded by bulk-insert over HTTPS,
a different path, so corpus retrieval kept working.
Root cause in one line: **"Redis is configured" was used as a proxy for "a
worker exists". They are different facts.**

**F2 — MINOR but real: exception-as-control-flow on every request.**
`app/main.py:390` calls `_jwt_auth.decode_token()` on whatever credential
arrives; an API key is not a JWT, so every API-key request raises
`DecodeError`, is swallowed, and logs a FULL TRACEBACK at WARNING. Confirmed
in live logs — one per request. Buries real warnings.

**F3 — TEST DEFECT (rule 4 violation, mine): a wrap-around green.**
S5 asserted `len(tool_calls) > 0` and passed on `tool_calls=[None]` — an event
whose name never resolved — while the answer visibly did the arithmetic in
prose and said the waste allowance was "Assumed". The server log independently
proves `tools=['construction_calc'] status=success`, so the SYSTEM is fine;
the ASSERTION could not tell an executed calculator from LLM prose. Must be
tightened to name the tool and match the tool_result payload before its green
means anything.

**F4 — MINOR: the SSE tool_call contract is inconsistent, and that is what
made F3 possible.** `runtime.py:2947` documents `tool_call` as
`{"name", "args", "id"}`; the streaming path at `runtime.py:3823-3827` emits
`{"tool", "args_preview"}`. Any consumer following the docstring reads `None`
for the tool name — which is exactly what the harness did. Recorded, not yet
fixed (rule 1: one defect at a time).

**Harness defect (corrected before it misled):** S3 first read chunk_count from
`/v1/projects/{id}/documents`, which never carries it; enrichment happens only
on the detail route (`app/routers/projects.py:339-345`). Fixed to poll detail.

### F1 fix — committed `9c9289e` on `fix/upload-ingest-reaches-index`

`enqueue_ingest` now requires an explicit `INGEST_WORKER_ENABLED` in addition
to `REDIS_URL`. Default is inline indexing, so a missing worker degrades to
slower ingestion rather than to none.

Red/green verified, and verified red for the RIGHT reason (a first attempt
failed on a foreign-key error in setup, which would have proved nothing):

| Test | old code | fixed |
|---|---|---|
| `test_upload_indexes_inline_when_no_worker_is_declared` | RED (no inline task scheduled) | GREEN |
| `test_reachable_redis_alone_never_implies_a_worker` | RED (`assert True is False`) | GREEN |
| `test_upload_uses_the_queue_when_a_worker_is_declared` | green | GREEN (fence holds both ways) |

Suite at the seam: 14 passed (`test_upload_reaches_the_index`,
`test_ingest_queue`, `test_ingest_worker`, `routers/test_upload`).

### F2 + F4 fixes — commit `98ef073`

- **F2** `_rate_limit_identity` now treats `InvalidTokenError` as the expected
  "this is an API key, not a JWT" case and stays silent; any *other* exception
  still logs loudly, so noise was not traded for blindness.
  `tests/test_rate_limit_identity_is_quiet.py` fences the observability
  property. Verified by stashing only the `main.py` change: RED on the old
  handler (`test_api_key_caller_is_identified_without_logging`), GREEN on the
  fix, with the still-loud fence passing in both states.
- **F4 corrected on the evidence.** My run-1 note called the SSE contract
  "inconsistent". It is not — both SSE events use `tool`, and the docstring I
  cited documents the separate `on_event` callback. The real defect is that the
  SSE shape is UNDOCUMENTED, and my harness read the wrong contract. Fixed
  additively: `name` is now emitted alongside `tool` on both events (frontend
  still reads `tool`, so non-breaking) and both contracts are documented in
  place.

### F5 — monitoring was blind to the client-facing hostname

health-watch probed only `the-fork-jn3t.onrender.com`. A dead Cloudflare
record, a dropped custom-domain binding, or an expired certificate all leave
the slug green — which is why theshovel.ai served 403/404 for five days
unnoticed. Added a separate `public-domain` job probing `theshovel.ai/livez`
on the same DB-free 15-minute cadence, kept separate so a red names the layer.

### Blast radius of F1 — MEASURED, and smaller than feared

`/v1/admin/corpus/collections` (note: the chunk field is `chunks`, not
`chunk_count` — a first parse using the wrong key wrongly reported five
zero-chunk collections, corrected here):

| Collection | Documents | Chunks |
|---|---|---|
| `drive_archive` (= `master_corpus` alias) | 7 | 2296 |
| `training_material` | 25 | 234 |
| `curated_kb` | 25 | 213 |
| `60cfc7bf` (campaign) | 1 | 1 |

58 documents, 2744 chunks — reconciles with `chunks_v2` = 2743 + the repaired
campaign doc. **Every document in the system is indexed.** So F1 destroyed no
existing data: nobody had uploaded through the app since the worker was
removed, which is also why it stayed invisible. It would have hit the client
on their first upload.

### Earlier row-count probe (retained)

`pilot-preflight`: 250 projects, 58 documents, `chunks_v2` = 2743,
`chunks` (legacy) = 0. The Master Corpus is intact because it was bulk-loaded
over HTTPS; it is specifically **app-uploaded** documents that went nowhere.
Repair path exists and is not destructive:
`POST /v1/admin/debug/project-reindex` and `/v1/admin/debug/doc-reindex`.

### F1 root cause CONFIRMED empirically (not just by code reading)

Ran `POST /v1/admin/debug/project-reindex?project_id=60cfc7bf` against the
still-unfixed production build. Result `{"indexed":1,"total_chunks":1}`, and an
independent read-back moved chunk_count 0 -> 1. Re-ran S4: the answer came back

> "The flange bolt torque specified for asset tag **QTX-7741** is **337 N.m**"
> ... *Source: COMMISSIONING RECORD - ASSET QTX-7741, chunk 0*

`needle_present=True, master_corpus_fallback=False`. The value 337 N.m is
fabricated and exists in no other corpus, so this answer could ONLY come from
the uploaded document.

**What this proves:** the extractor, chunker, embedder, pgvector store and
retriever were all healthy the whole time. The single broken link was the
routing decision in `enqueue_ingest`. That is the difference between a surface
symptom ("RAG is broken") and the mechanism, and it is why the fix is four
lines rather than a retrieval rewrite.

**Campaign status: OPEN.** PR #334 open with F1/F2/F4/F5. Rule 8 requires a run
that passes with all enforcement active; the fixes are not deployed, so S3/S4
still fail in production. S5 has been tightened (must name `construction_calc`
in both call and result, must carry both keys, must report ok) and S6 (export,
needle read back out of the docx XML) has been added but neither has been
exercised against a live run yet.
