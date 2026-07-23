<!--
PR template inspired by the body shape that worked in this repo's security
campaign (PRs #5–#8). Keep this template; fill out every section. PRs with
an empty body are blocked by .github/workflows/pr-quality.yml.

Skip this template only for trivial PRs (single typo, dependency bump);
mark them with "trivial:" in the title.
-->

## Summary

<!-- 2-5 lines: what changed. Imperative voice — "Add", "Fix", "Move", not
"Adds", "Fixes". Reference issues by number if any. -->

## Why

<!-- The reason this exists. What was broken / missing / mis-shaped before.
Link to the user report, the bug, the prior PR's "known follow-ups" entry,
or the roadmap doc. If this PR fixes a real bug, name the exploit /
regression scenario in one sentence. -->

## What's in this PR

<!-- For multi-change PRs, one bullet per change. Each bullet:
  - the change in 5-10 words
  - the file(s) it touches
  - the test(s) that cover it (or "uncovered — see Honest non-verification") -->

## What's NOT in this PR

<!-- Optional but strongly recommended. Lists items deliberately deferred —
follow-ups for a later PR, things that need a different environment or skill
set, items waiting on a separate decision. The PR #5 / #6 / #7 / #8
campaign worked because each PR named what it wasn't doing as well as what
it was. -->

## Test plan

- [ ] Tests added / updated for new behaviour
- [ ] Full suite: `pytest tests/ --ignore=tests/browser` — X passed, Y skipped
- [ ] <Manual verification steps if any>
- [ ] CI green

## Honest non-verification

<!-- Only when shipping unverified scaffolding (e.g. a script that needs a
GPU / cloud key the CI doesn't have). Name exactly what didn't run and how
the next consumer should verify. -->
