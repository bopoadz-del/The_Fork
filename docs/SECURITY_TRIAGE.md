# CodeQL Triage Log

Read this **before** triaging a CodeQL re-scan. Many alerts that look new are already-adjudicated false positives or accepted risks.

Each entry names the alert class, the location, the disposition (false positive / accepted / fixed), and the per-finding rationale. When CodeQL re-flags the same pattern, link the dismissal to this doc rather than re-deriving the rationale from scratch.

## Dismissed — false positive

### `js/insecure-randomness` on `projectSessionId`

**File**: `app/static/index.html`
**First flagged**: CodeQL first scan (May 2026), triaged in PR #12.
**Disposition**: Dismissed — not a security boundary.

`projectSessionId` is a per-tab session-grouping key, not a credential or an auth token. It identifies which tab's events belong together; it does not gate access to any resource. `Math.random()` is appropriate.

When CodeQL re-flags: confirm the use site is still per-tab grouping. If it ever gets used as an auth token or a deduplication key for sensitive operations, the dismissal is no longer valid — fix it then.

### `py/path-injection` in `drive_auth.py` / `file_crypto.py`

**Files**: `app/core/drive_auth.py`, `app/core/file_crypto.py`
**First flagged**: CodeQL first scan, triaged during the security audit (PR #5 review).
**Disposition**: Dismissed — guarded by `_safe_user` allowlist.

Path construction in these modules is gated by a `_safe_user` helper that rejects any user identifier not matching `^[a-zA-Z0-9_-]+$`. CodeQL's taint tracking doesn't recognize the allowlist as a sanitiser, so it flags the downstream `os.path.join` as a sink.

When CodeQL re-flags: confirm `_safe_user` is still called before every path construction site in the flagged file. If a new code path bypasses the allowlist, the dismissal does not apply — that's a real finding.

## Dismissed — test fixture noise

### `py/clear-text-logging-sensitive-data` (12 alerts in `tests/`)

**Files**: various `tests/test_*.py`
**First flagged**: CodeQL first scan, triaged via the CodeQL API in PR #11.
**Disposition**: Dismissed in bulk — test-fixture noise.

Test files set up fake credentials (`api_key = "test-key-123"`, `password = "hunter2"`) and then assert behaviour. CodeQL flags the fixture string as a logged credential. The fixtures are deliberately fake; no real credential is logged.

When CodeQL re-flags in a NEW location: check whether the flagged file is in `tests/`. If yes, dismiss with this rationale. If no, investigate — production code logging a sensitive value is a real finding.

## Fixed

### `js/xss-through-exception` and `js/xss-through-dom` (5 alerts in `index.html`)

**Files**: `app/static/index.html`
**Fixed in**: PR #12 (security: fix XSS in chat UI).

Five real XSS findings:

- `bubble.innerHTML = text` branch when `role==='error' && text.includes('<div')` — backend error message rendered as HTML. Removed; errors now go through `textContent`.
- `renderDrives()` / `renderDrivesList()` interpolated drive filenames, drive names, and drive icons into `innerHTML` template literals without escaping. Wrapped in `escapeHtml()`.
- Inline `onclick="selectDriveFile('${id}', '${name}')"` JS-string injection — replaced with `data-drive-id` / `data-file-name` attributes and a delegated `click` listener. No more inline JS string concatenation surface.

### `py/insecure-temporary-file` (3 alerts in `ocr.py` / `ocr_v2.py`)

**Files**: `app/blocks/ocr.py`, `app/blocks/ocr_v2.py`
**Fixed in**: PR #11 (security: CodeQL quick wins).

`tempfile.mktemp` returns a path that another process can squat before our `pix.save()` runs (TOCTOU race). Replaced with `tempfile.mkstemp` which creates the file atomically with `O_CREAT|O_EXCL`.

Subtle: the fix uses `os.close(fd); pix.save(tmp)` which closes the atomic fd before reopening by path. The atomic guarantee is "the filename was unique at creation time" — still correct for the TOCTOU finding. For new sites, prefer `tempfile.NamedTemporaryFile(delete=False)` and write into the open fd directly to avoid the close-and-reopen.

### `actions/missing-workflow-permissions` (2 alerts in `.github/workflows/`)

**Files**: `.github/workflows/test.yml`
**Fixed in**: PR #11.

Workflow now declares top-level `permissions: contents: read`. Both jobs drop write scopes they never used. Future workflows in this repo should default to this at the top of every file.

### XSS in `setOutcomes` (PR #18 — found by CodeQL during PR review)

**File**: `app/static/index.html` (line ~705)
**Fixed in**: PR #18 fixup commits.

`setOutcomes(html)` assigns its argument to `.innerHTML`. Twelve callers interpolated user-controlled file names (`${fileName}`, `${file.name}`, `${drive.name}`, `${folderName}`) into template literals without escaping. CodeQL's data-flow analysis traced four paths from DOM-sourced strings into the innerHTML sink. All twelve interpolations now wrap user-controlled values in `escapeHtml()`.

Pattern: any function that writes `.innerHTML = <expr>` is a XSS sink. Audit caller-side interpolations whenever such a function gains a new caller.

## Accepted — known residual risk

### Leaked DeepSeek API keys in git history

**Files**: `.env` and `render.yaml` (no longer in working tree)
**First flagged**: GitHub Secret Scanning alert #1.
**Disposition**: Accepted — keys are in git history; rotation is the owner's call.

PRs #13/#14 removed `.env` and `render.yaml`'s DeepSeek key from the working tree but did not rewrite git history. The keys remain recoverable via `git log -p`. The owner has explicitly opted to absorb the residual risk rather than rotate. Per memory file `feedback-the-fork-env-committed.md`, do not push on this.

Anyone reviewing CodeQL or Secret Scanning alerts about these keys in the future: this is acknowledged and intentional; do not re-open as an action item.

## How to add an entry

When you dismiss a CodeQL finding, copy one of the existing entries' shapes:

- **Alert class** (the CodeQL rule ID, e.g. `js/insecure-randomness`).
- **File(s)** affected.
- **First flagged** — when and in which PR.
- **Disposition** — "Dismissed — false positive", "Dismissed — accepted risk", "Fixed in PR #N", etc.
- **Per-finding rationale** — what made the dismissal correct. Without this, the dismissal is unreproducible the next time CodeQL re-scans.
