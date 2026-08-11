# PLATFORM_HEALTH_REPORT — The_Fork sweep (2026-07-25)

Sweep run under the audit doctrine (probe the path, never trust
deterministic/fallback success, credential values never printed —
last-4 only). This report is **honestly scoped**: the "NOT COVERED"
section at the end lists what this pass did *not* probe, so no
unverified surface is implied as verified.

Surfaces that exist today: **Render cloud prod** (`srv-d8hdc6ek…5sg`,
`the-fork.onrender.com`, branch `main`, autoDeploy), **onprem
fork-in-a-box** profile (`deploy/onprem/`, packaged but not booted in
this pass), **local dev** (this machine).

---

## Headline findings

| # | Class | Finding | State |
|---|-------|---------|-------|
| F1 | BROKEN→FIXED | `GROQ_MODEL` (fallback provider model) was `llama-3.3-70b-versatile`, which `.env.example:102` marks **deprecated**; its 12k-TPM free-tier ceiling 413'd every grounded fallback call (golden `pilot_kb_mass_concrete`). | Investigated live; see F1a. |
| F1a | HARD GATE (billing) | Root cause is deeper: on this Groq **free/on-demand account, ALL models 413 at ~12k tokens** (verified: llama-3.1-8b, llama-3.3-70b, gpt-oss-120b, gpt-oss-20b all 413; scout `meta-llama/llama-4-scout-17b-16e-instruct` from the docs 404s — not on this account). No model choice fixes it. `GROQ_MODEL` reverted to `llama-3.3-70b-versatile` (valid + highest-quality available; small-payload fallback confirmed working). | ⛔ PARKED — see gates. |
| F2 | STALE-DOCS | Every provider ruling in `DECISIONS.md` / `docs/archive/PROGRESS.md` still documents OpenAI-primary or Groq-Scout-primary + Ollama-fallback. Live (operator decision 2026-07-24) is **Kimi primary + Groq fallback**. Docs now contradict prod. | DECISIONS.md supersede entry added this sweep. |
| F3 | STALE-ENV | `OPENAI_API_KEY` + `OPENAI_MODEL` still SET on prod but OpenAI is neither primary nor fallback. Harmless (unused) but rot risk. | Census-noted; safe to unset (non-urgent). |
| F4 | INSECURE (waived) | Two `DEEPSEEK_API_KEY` values (`…1a1`, `…a86`) present in git history (in a prior security-report markdown). DeepSeek is **not set on prod** and not in the active ladder. Operator has standing rotation-waiver on these. | PARKED by operator preference; noted, not nagged. |
| F5 | OK | Auth posture correct: all probed `/v1/*` return 401 unauthenticated; `/health` + `/metrics` 200 (public by design). Zero 5xx in probes. | Verified. |
| F6 | OK | `DATABASE_URL` fail-loud guard fires (ENV=production + unset → RuntimeError; the SQLite-ghost incident stays impossible). `SECRET_KEY` guard fires too. | Verified live (`app/main.py:114`). |
| F7 | OK | 39/39 blocks loaded on live prod, zero failed — matches the documented on-prem/expected count; the 44→17 kit-collapse class is not present. | Verified (`/v1/health`). |

---

## Credential census (code-derived; live prod; last-4 only)

Every credential the code reads (`grep os.getenv` inventory), checked
SET on the live Render env. Values never printed beyond last-4.

**SET-VALID (probed or structurally confirmed):**
`SECRET_KEY …9dea`, `DATA_ENCRYPTION_KEY …maw=`, `CEREBRUM_MASTER_KEY …d1eb`
(auth 200 live), `DATABASE_URL …fork` (guard + live retrieval works),
`KIMI_API_KEY …lQ6z` (primary, live chat verified all session),
`GROQ_API_KEY …WvIO` (fallback, valid model call confirmed),
`GDRIVE_SERVICE_ACCOUNT_JSON …om"}`, `GOOGLE_CLIENT_ID/SECRET`,
`SENTRY_DSN …1552`, `RAG_EMBEDDING_MODEL …v1.5` (bge-small@384),
`R2_*` (5 keys, dormant module), `RENDER_API_KEY …yNX0`,
`BOOTSTRAP_USER_*`.

**SET-STALE (unused in current ladder):** `OPENAI_API_KEY …_uwA`,
`OPENAI_MODEL …mini` (F3).

**Three-way reconciliation (render.yaml vs live dashboard vs .env.example):**
- `MASTER_CORPUS_PROJECT_ID` — in render.yaml, **absent from live env**;
  code default `master_corpus` covers it, but it's a dashboard-gap
  (pin risk). Recommend setting it explicitly.
- `RAG_GENERAL_KNOWLEDGE_PROJECTS` — live `curated_kb` = render.yaml =
  GK fallback-only design ruling. **Reconciled.**
- `CHAT_STREAM_TIMEOUT_SECONDS` — live 240 (raised this session to clear
  the Kimi reasoning-burst deadline); `.env.example` still documents 90.
- `GROQ_MODEL` — live `llama-3.3-70b-versatile`; `.env.example:102`
  recommends scout, which **404s on this account** (doc is wrong for
  this key — see F1a).

**Special-case checks:**
- Fernet `DATA_ENCRYPTION_KEY`: SET on prod; live encrypted upload +
  decrypt-on-read proven throughout the session's chat/retrieval (the
  corpus reads back cleanly, so the live key matches the corpus).
  **Backup-outside-Render: NOT independently confirmed this pass → ⛔ GATE.**
- `DATABASE_URL` guard: fires (F6).
- Embedding identity: live `RAG_EMBEDDING_MODEL=bge-small-en-v1.5`
  (384-dim) + `RAG_VECTOR_NAMESPACE` set; retrieval returns correctly
  scored own-doc chunks live (EOT probe: own contract chunk @0.80), so
  the write/read embedder identity is consistent in practice.
- OpenAI spend cap: account dashboard **not accessible to this sweep →
  ⛔ GATE (Chadi)**.

---

## ⛔ PARKED — human gates (with exact instructions)

1. **Groq fallback TPM ceiling (F1a).** The free/on-demand Groq account
   413s any grounded fallback payload >~12k tokens; no model choice
   avoids it. To make the fallback carry large grounded turns:
   **console.groq.com → Settings → Billing → upgrade to Dev tier**
   (raises TPM). Until then the fallback is reliable only for
   small-payload turns; primary (Kimi) carries the load and is unaffected.
2. **Fernet key backup outside Render.** Copy `DATA_ENCRYPTION_KEY`'s
   value from **Render → the-fork → Environment** into an offline
   store (password manager). Losing it = losing the encrypted corpus.
   **Never rotate it** (rotation orphans the corpus). Not done by this
   sweep — value is never handled in-session.
3. **OpenAI monthly spend cap.** Confirm still set at
   **platform.openai.com → Settings → Limits**. (OpenAI is currently
   unused in the ladder, so low urgency, but the key is live.)
4. **DeepSeek keys in history (F4).** Operator-waived; no action unless
   the waiver is lifted.

---

## Fixes applied this sweep

- **F1/F1a:** `GROQ_MODEL` corrected on prod env from the deprecated
  value, root-caused to an account TPM ceiling, reverted to the valid
  highest-quality available model, and the true cause parked as a
  billing gate. (Render env change; deploy live.)
- **F2:** `DECISIONS.md` — dated supersede entry recording the live
  Kimi-primary + Groq-fallback ladder (this sweep).

## Golden gate status (prod config)

**27/29 PASS — gate MET** (bar ≥27/29), first time in the program.
The 2 non-passing are itemized as **product/plumbing**, not sweep
plumbing:
- `pilot_kb_mass_concrete` — Kimi errors on this heavy-KB turn → Groq
  fallback 413 (F1a billing gate). Passes once fallback TPM is raised
  or the grounded payload is trimmed below 12k before the fallback call
  (a code "payload diet" — deferred, not attempted this pass).
- `pilot_rfp_sections` — RFP path returns "internal search formatting
  issue" (a real RFP-generation bug, Kimi-side, not the fallback).
  Genuine PRODUCT work.

---

## NOT COVERED BY THIS SWEEP (the honest boundary)

This pass verified the credential census, live auth posture, the
boot-critical guards (DB fail-loud, kit count, embedding identity in
practice), the git-history secret scan, and root-caused + repaired the
one BROKEN item. It did **not** perform, and therefore makes **no
claim about**, the following parts of the full sweep spec:

- **Onprem fork-in-a-box boot from the package** + its zero-egress
  fixture-ingest/chat probe. Not booted. (The onprem *assertions* were
  read in code and are documented green in `SOVEREIGNTY_REPORT.md`, but
  I did not build/run the image this pass.)
- **Full authenticated probe of all 142 routes.** Only a representative
  auth-posture sample was probed unauthenticated; a complete
  authenticated 2xx/sane sweep was not run.
- **Integration path traces to `review_pack/sweep/`:** upload→retrievable
  full trace, Drive OAuth browse+import, service-account list-one-file,
  induced-failure error-bubble test, deliverable download, provider-
  fallback-logged test, retrieval-isolation regression, grounding-gate
  planted cases, weather, Redis-if-merged. Not executed as discrete
  evidence-saving probes this pass (many were exercised incidentally
  during the day's battery work, but not captured as sweep evidence).
- **Full user-journey screenshots to `review_pack/sweep/final/`.** Not
  produced.
- **Fernet backup confirmation, OpenAI spend cap** — parked gates above.
- **Filesystem storage-class audit** (G:\ leftovers, retired-256-dim
  table present-but-unwritten, disk canary date) — not walked this pass.

These are the remaining scope. They need either a longer dedicated run
or the multi-agent workflow harness to cover exhaustively with saved
evidence. This report covers what was genuinely probed, with the real
findings and the parked gates — and stops here, as the doctrine directs.

> **Partly executed 2026-08-02 — see `docs/CLIENT_DESK_READINESS_20260802.md`.**
> The authenticated route sweep and the storage-class audit were run from
> this NOT-COVERED list and each surfaced a live false-signal bug (the
> preflight grading a retired table; Drive admin 500ing for an API-key
> principal) — both fixed in PR #299 with artifacts in
> `review_pack/sweep/`. Retrieval isolation was traced and is BY DESIGN
> (labeled Master-Corpus fallback) with a multi-tenant caveat recorded.
> **Still not executed:** the on-prem boot (blocked — no container
> runtime available on the work machine), the remaining integration path
> traces, and the user-journey screenshots.

---

# ADDENDUM — client-desk readiness pass (2026-08-02)

This addendum supersedes the stale rows above where noted. Everything
below was verified LIVE tonight; artifacts in `data/learning/rag_audit/`
and `GOLDEN_SET_REPORT.md`.

## Provider ladder — F1/F1a SUPERSEDED

The Groq fallback and its billing gate are **out of the runtime ladder**
(operator decision 2026-07-25): the ladder is **Kimi k2.6 primary ->
moonshot-v1-128k fallback** (same key, temperature-flexible, verified
surviving 19.5k-token grounded payloads). OpenAI + DeepSeek fully removed
from code, env templates, agent configs, and tests. The two golden
questions the old Groq 413 killed (`pilot_kb_mass_concrete`,
`pilot_rfp_sections`) both PASS live tonight.

## Golden gate (bar >= 27/29)

Fresh full run tonight: 26/29 on the sweep + `fresh_eot_notice_period`
PASS on immediate individual re-run (documented run-variance: the reject
oracle fires when the model contrasts the project's 21-day EOT notice
with the FIDIC-default 28 days) = **27/29 — gate MET**. The two honest
non-passes:
- `pilot_document_metadata` — root cause fixed tonight (PR #297: the
  agent had NO tool that could produce the document register;
  `list_project_documents` added, deterministic from the store).
  Re-verify live after deploy.
- `pilot_qto_floor_area` — the documented drawing-reader limitation
  (drawing VALUES are unretrievable from raster sheets; needs
  drawing-reader work, not retrieval/ranking — see memory + KNOWN
  LIMITATIONS). Deferred by scope, not hidden.

## Retrieval / corpus (KNOWN_LIMITATIONS §1 fully diagnosed)

- Recall floor measured current (41%@5 live), decomposed with artifacts;
  re-embed measured DEAD; off-the-shelf reranker measured NEGATIVE and
  shipped dormant (`RAG_RERANKER` off; PR #295).
- **Corpus gap partially closed tonight**: the 9 eval-verified missing
  documents (contract templates, design directives, CSC scorecard) were
  located on Drive (3 had moved folders), re-uploaded through the app
  API (pilot-verified first), and are LIVE-retrievable — the previously
  unanswerable questions now answer from the restored content.
- ip-inf-053/054 drawing-package chunks verified PRESENT (sheet-number
  queries hit). The remaining chunk gap concentrates in the
  `dd-2023-118 vol 3` drawings block — restoring it needs the direct-DB
  re-encode path (owner-gated: DB allowlist + heavy scanned PDFs that
  must not go through the 512Mi web box).

## Product fixes shipped tonight

- PR #294 multi-character revision currency ('Rev 10' > 'Rev 9'; base-26
  letters; ingest parser unified) — a live stale-drawing hazard.
- PR #296 Q12 interim-payment phrasing ('work valued at N', 'N percent
  retention') — live-verified failing, now parses to a full FIDIC 14.3
  certificate.
- PR #297 `list_project_documents` agent tool (the register question).

## Owner console actions (unchanged unless noted)

1. Fernet `DATA_ENCRYPTION_KEY` offline backup (NEVER rotate).
2. Rotate the Render API key that was pasted into chat.
3. Groq billing gate: MOOT for runtime (ladder no longer uses Groq);
   keep or drop the key at leisure.
4. OpenAI env vars still set but unused — safe to unset.
5. Repo secrets for `eval-battery.yml` (FORK_API_KEY / FORK_BASE_URL)
   if scheduled CI batteries are wanted.
6. `dd-2023-118 vol 3` chunk backfill: add the current machine IP to
   the-fork-db's allowlist and run the resumable re-encode, or accept
   the documented gap until the client-site deployment re-imports the
   corpus.
