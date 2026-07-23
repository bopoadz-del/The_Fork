# Accepted dependency risks (Dependabot / OSV)

Review date: **2026-06-13** (updated from 2026-06-12 platform-hygiene pass). Production Render image installs **`requirements.txt` only** (see `Dockerfile`).

---

## Alert 1 — `deep-translator` — **RESOLVED** (2026-06-13)

| Field | Value |
|-------|--------|
| **Was** | Direct dep in `requirements.txt` — PYSEC-2022-252 |
| **Fix** | Removed `deep-translator`; `app/blocks/translate.py` calls the public Google Translate HTTP endpoint via `requests` (same `translate.googleapis.com` client=gtx path). |
| **Production** | `requirements.txt` no longer lists `deep-translator`. |

---

## Alert 2 — `torch` in `requirements-cv.txt` (optional)

| Field | Value |
|-------|--------|
| **Manifest** | `requirements-cv.txt` (optional CV tier) |
| **Dependency type** | **Transitive** — `ultralytics` → `torch==2.12.0` |
| **Advisory** | [GHSA-rrmf-rvhw-rf47](https://osv.dev/GHSA-rrmf-rvhw-rf47) (LOW, local `torch.jit.script`) |
| **Production exposure** | **No** — not in `Dockerfile` |

**Why not bump:** No patched PyTorch release beyond affected `<=2.12.0`. Lock recompiled 2026-06-13; pin unchanged.

**Dismiss comment:** `Accepted: optional CV lockfile only, not in production image. GHSA-rrmf-rvhw-rf47 has no patched torch release yet; local jit.script vector.`

---

## Alert 3 — `torch` in `requirements-ml.txt` / `requirements-rag.txt` (optional)

Same advisory **GHSA-rrmf-rvhw-rf47**, optional ML/RAG install paths only. Locks recompiled 2026-06-13.

**Dismiss comment:** `Accepted: optional ML/RAG lockfiles only, not shipped on Render. No torch fix version; revisit when PyTorch >2.12.0 addresses GHSA-rrmf-rvhw-rf47.`

---

## Production manifest status

| Manifest | Status |
|----------|--------|
| `requirements.txt` | **Clean** — no open OSV alerts after deep-translator removal |
| `frontend/package-lock.json` | Clean |
| `requirements-cv.txt` | torch GHSA (optional) |
| `requirements-ml.txt` | torch GHSA (optional) |
| `requirements-rag.txt` | torch GHSA (optional) |

---

## Revisit triggers

1. **torch:** PyTorch publishes a version outside GHSA-rrmf-rvhw-rf47 — re-run `scripts/compile-requirements.sh`.
2. **Quarterly:** scan `requirements.txt` with osv-scanner on main.
