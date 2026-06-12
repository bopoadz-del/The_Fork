# Accepted dependency risks (Dependabot / OSV)

Review date: **2026-06-12**. Scanned with [osv-scanner](https://github.com/google/osv-scanner) v1.9.0 against all lockfiles. Production Render image installs **`requirements.txt` only** (see `Dockerfile`).

GitHub shows **3 open low-severity Dependabot alerts**. None are fixable today with a version bump that clears the advisory. Dismiss each in **Security → Dependabot** as *Tolerable risk* using the rationale below.

---

## Alert 1 — `deep-translator` (production)

| Field | Value |
|-------|--------|
| **Manifest** | `requirements.txt` / `requirements.in` |
| **Dependency type** | **Direct** (`deep-translator>=1.11.0`) |
| **Pinned** | `1.11.4` (latest on PyPI) |
| **Advisory** | [PYSEC-2022-252](https://osv.dev/PYSEC-2022-252) |
| **Used by** | `app/blocks/translate.py` (Google Translate, no API key) |
| **Production exposure** | **Yes** — shipped in Render Docker image |

**Why not bump:** PyPI latest is already `1.11.4`. The advisory documents a **2022 account-compromise / malicious release** incident; OSV marks all `1.x` versions affected and publishes **no `first_patched_version`**. There is no newer clean release to upgrade to.

**Risk acceptance:** Installed via pinned version (`==1.11.4`) from PyPI over HTTPS. No hash verification enforced at install time (`requirements.txt` is not compiled with `--generate-hashes`; the Dockerfile runs plain `pip install -r requirements.txt`). Risk accepted as historical supply-chain incident with no runtime RCE vector in normal translate-block usage. Replacing the library (e.g. direct HTTP to Google Translate) is a feature change, not a patch bump — deferred.

**Dismiss comment:** `Accepted: deep-translator ==1.11.4 is latest on PyPI; PYSEC-2022-252 has no patched release. Version-pinned PyPI install (no hash verify); tracked in deploy/DEPENDENCY_RISKS.md.`

---

## Alert 2 — `torch` in `requirements-cv.txt`

| Field | Value |
|-------|--------|
| **Manifest** | `requirements-cv.txt` (optional CV tier) |
| **Dependency type** | **Transitive** — direct dep is `ultralytics`; pulls `torch==2.12.0` |
| **Advisory** | [GHSA-rrmf-rvhw-rf47](https://osv.dev/GHSA-rrmf-rvhw-rf47) (GitHub severity: **LOW**) |
| **Production exposure** | **No** — not in `Dockerfile` / Render deploy |

**Issue:** Memory corruption via `torch.jit.script` — **local** attack (`AV:L`), low impact.

**Why not bump:** Advisory lists **no fixed version**; `2.12.0` is the current pin and is within the affected range (`<=2.12.0`).

**Dismiss comment:** `Accepted: optional CV lockfile only, not in production image. GHSA-rrmf-rvhw-rf47 has no patched torch release yet; local jit.script vector.`

---

## Alert 3 — `torch` in `requirements-ml.txt` / `requirements-rag.txt`

GitHub may show one alert per manifest; osv-scanner reports the same GHSA on both:

| Manifest | Dependency type | Direct dep |
|----------|-----------------|------------|
| `requirements-ml.txt` | **Direct** `torch>=2.2.0` in `requirements-ml.in` | LoRA / Tinker training stack |
| `requirements-rag.txt` | **Transitive** via `sentence-transformers` | Optional legacy RAG layer (production uses model2vec in main `requirements.txt`) |

Same advisory **GHSA-rrmf-rvhw-rf47**, same pin `torch==2.12.0`, **not in production Docker image**.

**Why not bump:** No patched PyTorch release available beyond affected range.

**Dismiss comment:** `Accepted: optional ML/RAG lockfiles only, not shipped on Render. No torch fix version; revisit when PyTorch >2.12.0 addresses GHSA-rrmf-rvhw-rf47.`

---

## Manifests with no open alerts

| Manifest | osv-scanner |
|----------|-------------|
| `frontend/package-lock.json` | No issues |
| `requirements.txt` (except deep-translator above) | Clean |

---

## Revisit triggers

1. **deep-translator:** PyPI publishes a version explicitly outside PYSEC-2022-252 range, or we replace `translate` block with another backend.
2. **torch:** PyTorch release notes / GHSA list a fixed version — re-run `pip-compile` on affected `requirements-*.in` files and close alerts.
3. **Quarterly:** `osv-scanner -r /workspace` on main.

## Dismiss in GitHub (operator)

1. Repo → **Security** → **Dependabot** (3 open, low).
2. For each alert → **Dismiss alert** → **Tolerable risk**.
3. Paste the dismiss comment from the matching section above.

The Cursor cloud token cannot call the Dependabot alerts API (`403 integration`); dismissal must be done by a repo admin in the UI.
