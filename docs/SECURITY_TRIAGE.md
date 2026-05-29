# Security Triage — CodeQL Dismissal Rationale

When CodeQL runs (push to `main`, weekly cron, or a re-baseline after a rule update), it produces a list of alerts. Some are real and get fixed in dedicated PRs (see history of `#11`, `#12`). The rest are dismissed in the CodeQL UI as false-positives — but the UI dismissal lives on GitHub, not in the code, and is lost on a repo move, an organisation transfer, or a fresh first-scan in a fork.

This doc is the durable record of those decisions. If a CodeQL alert appears that matches one of the entries below, **the rationale has already been adjudicated** — don't re-triage from scratch.

The format intentionally cites concrete files/lines so a future reviewer can confirm the code still matches the rationale. When the cited code changes, revisit the entry.

---

## Dismissed alerts

### `js/insecure-randomness` — `app/static/index.html:1766`

- **Rule**: insecure random number generator
- **Line**: `const projectSessionId = 'proj-' + Math.random().toString(36).slice(2, 11);`
- **Why dismissed**: Used as a per-tab grouping key for streaming endpoints. Not auth, not session identity, not signed, not used to gate access. The server treats it as opaque — any string would do; randomness only spreads concurrent tabs across separate streams.
- **What would change the dismissal**: if the key is ever read server-side to make a trust decision (e.g. "this tab owns this session"), the rule fires for real and we'd need `crypto.getRandomValues` instead.
- **Note in code**: yes (PR-B). Future re-scans can match comment text.

### `py/path-injection` — `app/blocks/drive_auth.py`

- **Rule**: external-controlled path access
- **Why dismissed**: paths originate from authenticated user session state (OAuth refresh tokens written by the server side), not from request bodies. `pathlib.Path(...)` joins with `DATA_DIR` which is operator-set via env, not request-derived.
- **What would change the dismissal**: any code path that lets an unauthenticated request control a filename segment.

### `py/path-injection` — `app/blocks/file_crypto.py`

- **Rule**: external-controlled path access
- **Why dismissed**: same shape as `drive_auth.py` — file paths come from server-resolved doc IDs, not raw request input. The doc-ID-to-path mapping lives in `vector_store.chunks` (server-controlled).
- **What would change the dismissal**: exposing `decrypt_at(path)` to a route where `path` comes from a query string.

### Multiple alerts — `tests/**`, `data/**`

- **Rule**: various
- **Why dismissed**: test fixtures and seed data are not shipped to production. Files under `tests/` contain intentional bad-input strings (the test's payload), and `data/` holds repo-local sample documents.
- **What would change the dismissal**: anything in `tests/` being imported by `app/` at runtime, or `data/` shipping in the container image.

---

## Workflow conventions

For all new GitHub Actions workflows in `.github/workflows/`:

- `permissions: contents: read` at the top of every job by default. Add granular `write` scopes only where needed (e.g. `packages: write` for GHCR publish, `pull-requests: write` for auto-comment bots). This is enforced as a one-line check during code review — see `.github/workflows/CONVENTIONS.md`.

For multi-arch builds:

- Native arm64 runners only. **Do not use `linux/amd64,linux/arm64` in a single `docker/build-push-action` step backed by QEMU** — QEMU emulation runs ~5-10× slower and routinely overruns the GitHub Actions cache SAS-token window (cf. PR #23's revert of `linux/arm64` from the multi-arch line PR #10 introduced).

---

## PR #14 — known-leaked keys in git history

PR #14 untracked `.env` and `render.yaml` so new commits don't leak. The keys that were committed before PR #14 **remain retrievable via `git log -p -- .env render.yaml`**. Rotation status:

| Key (redacted — last 4 chars only) | Last seen committed | Rotation status |
|---|---|---|
| `DEEPSEEK_API_KEY  sk-…b1a1` | Before PR #14 | Not rotated (per operator preference — see `the-fork-env-committed` memory) |
| `DEEPSEEK_API_KEY  sk-…fa86` | Before PR #14 | Not rotated (same) |

The operator has explicitly opted to absorb the residual risk rather than rotate. This doc records that decision so it isn't re-litigated on every retro.

**Why suffixes only.** Committing the full unrotated values into this doc at HEAD would undo PR #14's intent — every clone, fork, and secret-scanner would read them in plaintext from the current tree, not from history. The last-4-chars suffix disambiguates WHICH row of the table refers to which key (against the full values still in `git log -p -- .env`) without re-shipping the live credential into the working tree. Codex flagged this on PR #29's first review (P1); the redaction was pushed to that branch but PR #29 merged before the redaction commit landed in `main`, so the full values are currently in `main`. This PR fixes that gap as part of its conflict resolution on this file.

---

## When CodeQL files a NEW alert

1. Cross-reference against this doc. If matched, the rationale is durable — dismiss in the UI and link the matching entry above in the dismissal note.
2. If unmatched, triage as a fresh alert. Open a PR per fix (PRs #11 + #12 are the precedent for scoping).
3. If the alert turns out to be a false positive after investigation, **add an entry here before dismissing in the UI**. The doc is the source of truth; the UI is the operational shortcut.

---

## sqlite-vec `fast_search` field (not a CodeQL item, but durable rationale)

The `fast_search: bool` field surfaced at `app/routers/rag.py:40,87` reports whether sqlite-vec is in use. After the PRs #19-#23 retrospective, the read path was numpy-only and the write path was populating `vec_chunks` for nothing — pure overhead. `_try_load_vec` now returns `False` unconditionally, so `fast_search` always reports `False`.

The field stays in the API response schema (removing it would be a breaking change for the frontend). When `search()` is wired to vec0 with a per-project post-filter (planned at >10k chunks per project), restore `_try_load_vec`'s probe and re-add the write mirrors in lock-step with the read.
