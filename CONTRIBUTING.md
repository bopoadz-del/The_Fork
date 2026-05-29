# Contributing to The Fork

Short guide for anyone (human or AI agent) opening a PR.

## Pull-request template

`.github/pull_request_template.md` is loaded automatically when you open a PR. Fill out every section. PRs with an empty body are rejected by `pr-quality.yml`.

The body shape that worked best in this repo's history is **PR #5** ("Security hardening: IDOR, auth gaps, SSRF, path traversal"). Use it as a reference:

- A short summary at the top.
- A per-finding paragraph for each substantive change, naming the issue, the fix, and the regression test.
- An explicit **"What's NOT in this PR"** list of follow-ups deferred to a later PR.

The campaign that ran across PRs #5–#8 was reviewable because each PR named what it wasn't doing as clearly as what it was. PRs #1, #2, and #3 are the cautionary opposite — large, undocumented, permanently opaque to audit.

## PR sizing

There's no hard limit, but a few soft rules of thumb derived from review experience:

- **Under ~500 LOC of meaningful change** is comfortably reviewable.
- **500–3000 LOC** needs a structured body (per-finding paragraphs, file-by-file rationale).
- **Over 3000 LOC** is a red flag. If the body lists three independent workstreams (as PR #3 did), split into three PRs — each can be reverted independently and each is small enough for a real review.

The CI gate warns at 5000 LOC. If you need to ship past that, label the PR `large-by-design` and explain in the body why splitting doesn't work for this change.

## Test coverage

The CI gate is `--cov-fail-under=25` (regression floor, not a target). It exists to prevent further coverage regression, not to bless the current level.

For new code:

- **New files under `app/core/` should ship with ≥50% line coverage.** These are the platform's load-bearing layer; PR #8 introduced the gate after the security audit found seven endpoints splatting raw `dict` into block `execute()` calls with no coverage.
- **New blocks under `app/blocks/`** need at least the happy path tested. The resilient block loader (PR #8) means a broken block load is non-fatal at app startup, but it's still a hidden bug.
- **Tests for new code should use the `isolated_data_dir` fixture pattern** established by the hydration work — fresh `DATA_DIR` per test, module-level `_initialized` flags reset. Don't write tests that share state via `/tmp` or the live `data/` directory.

## Block output contracts

Blocks compose into chains via `OrchestratorBlock`. A chain step that produces a JSON dict (e.g. `translate` returning `{"translated": "...", ...}`) automatically gets its primary text unwrapped before flowing into a text-expecting next step (`chat`, `translate -> chat`, etc.).

The unwrap order is:

1. **Producing block's declared `text_output_field`** (class attribute on `UniversalBlock`). If set, this key wins.
2. **Global priority-ordered fallback list** in `app/blocks/orchestrator.py:_TEXT_OUTPUT_FIELDS` (`text`, `translated`, `response`, ...).
3. **Single-string heuristic**: if exactly one value in the dict is a non-empty string, return it.

If your block's canonical text lives under a key that's NOT in the global list, declare it:

```python
class MyBlock(UniversalBlock):
    name = "my_block"
    text_output_field = "my_canonical_key"  # add this
```

Even if the key IS in the global list (e.g. `translated`), declaring it explicitly is preferred — it locks the contract and survives reordering of the global tuple.

Test the override in `tests/test_chain_text_output_field.py` and the legacy global-list path in `tests/test_chain_json_text_coercion.py`.

## Security follow-ups

`docs/SECURITY_TRIAGE.md` captures the CodeQL dismissal rationales from PRs #11/#12/#14. Read it before re-triaging a CodeQL re-scan — many alerts are already-adjudicated false positives.

## Direct pushes to main

By default, all changes land via PRs. The repo owner has opted into allowing direct pushes to `main` in personal Claude Code sessions (see `.claude/settings.local.json`), but the team norm is still PR-first — direct pushes bypass CI, CodeQL, and any review.

## Trivial PRs

Single-typo fixes, dependency bumps, or label changes can skip the PR template by adding the `trivial` label. The `pr-quality.yml` gate honors it.
