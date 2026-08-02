# Client-desk readiness verdict — 2026-08-02

Scope of this pass: the items `PLATFORM_HEALTH_REPORT.md` lists under
**"NOT COVERED BY THIS SWEEP (the honest boundary)"**, plus the open
dependency alerts. That NOT-COVERED list is what "all aspects, not RAG
only" actually means — it is the only definition of remaining work that
was already written down and agreed, so it is the one used here.

**Verdict: NOT 100%. Code-complete on everything code can close; five
items are owner-gated and one could not be executed on this machine.**
Each line below is either verified with a named artifact, or explicitly
marked unverified with the reason. Nothing is claimed that was not run.

---

## 1. What this pass found and fixed

Two live **false-signal** bugs — both found by executing NOT-COVERED
items, both of the same family: a check that reports success without
measuring the thing it claims to measure.

### 1a. `pilot-preflight` graded a retired table (PR #299)

Live prod before the fix:

```
chunks_embedding_type : vector(256)
embedding_dim_ok      : True
row_counts.chunks     : 0
```

`chunks` has been empty since the 2026-07-12 v2 migration; every write
goes to `chunks_v2` at `vector(384)`. The operator readiness gate read
the width off a **dead table** and compared it to a hardcoded
`vector(256)`. It answered `True` while telling us nothing about the
store that actually serves retrieval, and would have stayed green with
`chunks_v2` missing, empty, or the wrong width.

This is the same class already fixed once in `admin.py` for the corpus
count — where reading `chunks` showed 0 chunks for every project and
"invited a destructive re-index click". The preflight was left behind.

Now reports the active table, its real width, the legacy width, and
compares against the **loaded embedder's** dim. Cold embedder returns
`null`, never a green boolean from an absent measurement.

Three operator-facing docs were telling operators to verify against the
retired table and were corrected: `docs/backup-and-recovery.md` (live
runbook), `deploy/PILOT.md` (dated history — annotated SUPERSEDED, not
rewritten), `README.md` architecture diagram.

### 1b. Drive admin routes returned 500 for an API-key caller (PR #299)

```
GET /v1/admin/drive/scan -> 500 INTERNAL_ERROR
KeyError: 'user_id'
```

`require_api_key` accepts two principal shapes and **normalises only
one**. The JWT branch resolves a real user and includes `user_id`; the
API-key branch returns the `auth_manager` key record — `user`/`tier`/
`role`, no `user_id`. `CEREBRUM_MASTER_KEY` is minted with
`role: "admin"`, so a master-key caller passed `_require_admin` and then
died indexing `auth["user_id"]`.

Effect: the master key **could not use the admin Drive workflow at all** —
neither `drive/scan` nor `approve-from-drive` (which also used
`auth["user_id"]` as the owner of the project it creates).

The existing tests missed it because their fixture overrides
`require_api_key` with a dict that *has* `user_id` — a fixture more
generous than production. The new tests use the exact dict
`auth_manager` mints for the master key.

### 1c. The class behind 1b was triaged in full — it is closed

79 `auth["user_id"]` hard-index sites exist. An AST triage
(`scratchpad/auth_triage.py`) mapped every one to its route's auth
dependency:

| Dependency | Sites | Exposed? |
|---|---|---|
| `require_api_key` | 4 (all Drive admin) | **was exposed — fixed in #299** |
| `require_user` | 75 | No — safe by construction |
| helper / closure | 5 (`agents.py`) | No — callers all use `require_user` |

`require_user` is safe for a better reason than "JWT-only": it accepts
API keys too, but **normalises** them to the singleton system user, so
`user_id` is always present. `require_api_key` does not normalise. That
asymmetry is the root cause and is worth closing at the dependency
level as hardening — see §5.

**Exposed sites after #299: 0.**

---

## 2. Verified green (with artifacts)

| Aspect | Result | Evidence |
|---|---|---|
| Authenticated route sweep | 59 GET operations, 41 OK, **1** 5xx (fixed above) | `review_pack/sweep/route_sweep_20260802.{json,txt}` |
| Document-register golden question | **PASS live** after PR #297 deploy | `golden_set_gate.py --only pilot_document_metadata` |
| Live vector store | `chunks_v2`, `vector(384)`, non-empty; legacy `chunks` = 0 rows as expected | `/v1/admin/debug/pilot-preflight` |
| Live service health | 200 throughout the sweep | route sweep log |
| Deploy currency | `dep-d9naa9m…` live at `d0ade99` (includes #297) | Render deploys API |

**Route-sweep boundary, stated plainly:** 79 **mutating** operations
(POST/PUT/PATCH/DELETE) were deliberately **not** probed. This ran
against the live client service, and a mutating sweep on a live corpus
is the cascade-delete class this repo has already survived once. The
artifact records the unprobed list rather than implying full coverage.

---

## 3. Dependency alerts — measured, not asserted

Baseline at session start: **15 alerts (7 high)**.

Merged: #260 react-router 7.18.1, #271 postcss 8.5.23, #293
brace-expansion 5.0.9. #299 carries pyasn1 0.6.3 -> 0.6.4 (three HIGHs).

**Expected residual after #299 merges: exactly one HIGH, open.** Verify
with `gh api repos/bopoadz-del/The_Fork/dependabot/alerts` filtered to
`state=="open"` and severity `high`.

| Alert | Disposition |
|---|---|
| react-router `>=7.12.0,<8.3.0` (HIGH, GHSA-qwww-vcr4-c8h2) | **OPEN — assessed unreachable, NOT cleared.** The advisory is an *RSC-mode* CSRF bypass. `frontend/` is a client-side Vite SPA using `BrowserRouter`; zero RSC markers, no `use server`, no react-server imports, no server actions. Clearing it needs the v8 **major** migration, which is not justified for an unreachable path on a live client app. The alerts API will keep returning it — that is expected, not an oversight. |
| pytest `<9.0.3` (MEDIUM) | **Accepted with measured evidence.** The `pytest<9.0` pin is load-bearing, not defensive: pytest 9 + pytest-asyncio 1.x produced **27 failures** the pinned stack does not have (`scratchpad/pytest9.log`; the same 4 files pass under pytest 8). pytest *is* installed in the production image (the Dockerfile installs `requirements.txt`) but is never invoked there, and the advisory is tmpdir handling, which requires running pytest. See §5 for the structural fix. |
| torch `<=2.12.1` (LOW ×3) | **Unreachable.** `torch.jit.script` has zero call sites in `app/` or `scripts/`. |
| @babel/core (LOW) | Dev-time build tool only. |

---

## 4. Test-suite honesty (KNOWN_LIMITATIONS §8)

The "17 skip/xfail files" were enumerated. Almost all are legitimately
environment-gated and are *not* hidden failures: Postgres-only cascade
rules, Node/tsc not installed, POSIX-only resource limits, missing
optional IFC/XER fixtures, and flag-off no-op guards.

Two `xfail` markers were the real question — both said "GK crowds
project docs out of top-5". Re-checked this pass:

- `test_doc_search_api.py::test_search_returns_ranked_results` —
  **XPASSes now.** The GK MARGIN+FOLD ranking work fixed it. Because the
  marker was `strict=False`, the XPASS was tolerated silently and the
  restored coverage went unnoticed. **Marker removed**, so a GK-crowding
  regression fails loudly again.
- `test_doc_index.py::test_search_uses_hybrid_retriever` — **still
  genuinely xfails.** Marker kept, reason rewritten to record that it is
  now the *last* remaining GK-crowding case rather than a stale marker.

Also observed: `test_backfill_layers.py::test_backfill_tags_unlayered_chunks`
fails when run alongside the three GK/RAG files but passes in isolation —
**cross-file test contamination**, order-dependent. Real but low
severity; recorded rather than papered over.

---

## 5. Recommended hardening (found this pass, not shipped)

Deliberately not done tonight — each changes shared behaviour on a live
client system and deserves its own verified deploy:

1. **Normalise `require_api_key`.** It should return a principal with the
   same guaranteed keys as `require_user`. Today the asymmetry means any
   *future* `auth["user_id"]` on an api-key route is a latent 500.
2. **Stop shipping the test framework in the production image.** Moving
   test deps out of `requirements.txt` removes the pytest alert from the
   prod surface entirely and shrinks the image, without the 27-failure
   pytest-9 migration.
3. **torch version drift.** The Dockerfile pins `torch==2.5.1` while
   `requirements-cv/ml/rag.txt` pin `2.12.0` — the image and the lock
   files disagree about the ML stack. Harmless today; a trap later.
4. **`/openapi.json` is publicly readable on prod** (200 unauthenticated).
   It discloses the full API surface. Consider gating it for a
   client-desk deployment.

---

## 6. Could NOT be verified on this machine

- **On-prem fork-in-a-box boot + zero-egress probe.** `deploy/onprem/`
  is packaged but has still never been built or booted. **Blocked, not
  deferred:** this machine has no container runtime — `docker`, `podman`
  and `nerdctl` are all absent, and `wsl` fails with
  `Class not registered`. Needs an operator with Docker installed. The
  sovereignty claims in `SOVEREIGNTY_REPORT.md` therefore remain
  **code-read only, never executed**.
- **Integration path traces as discrete saved evidence** (Drive OAuth
  browse+import, induced-failure error bubble, provider-fallback-logged,
  grounding-gate planted cases). Several require mutating a live client
  corpus; not run this pass. Still open from the previous report.
- **Full user-journey screenshots** to `review_pack/sweep/final/`. Not
  produced.

---

## 7. Owner-gated — code cannot close these

1. **Fernet `DATA_ENCRYPTION_KEY` offline backup.** Copy from Render ->
   Environment into an offline store. **Never rotate** — rotation
   orphans the encrypted corpus.
2. **Rotate the Render API key** that was pasted into chat.
3. **OpenAI env vars** still set but unused — safe to unset.
4. **Repo secrets for `eval-battery.yml`** (`FORK_API_KEY` /
   `FORK_BASE_URL`) if scheduled CI batteries are wanted.
5. **`dd-2023-118 vol 3` chunk backfill** — needs the DB allowlist plus
   the direct-DB re-encode path (heavy scanned PDFs must not go through
   the 512Mi web box).

---

## 8. Bottom line

The platform is **code-complete for client-desk deployment** on
everything this pass could reach, and two real live defects were found
and fixed that would each have produced a false "all good" reading in
front of a client.

It is **not 100%**, and no honest report can say so while:

- one HIGH alert stays open (assessed unreachable, not cleared),
- the on-prem sovereignty package has never actually been booted,
- and five items require the operator's own hands.

Those are enumerated above with the exact action for each.
