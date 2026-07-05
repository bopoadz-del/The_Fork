# Program progress log

Living log of the autonomous work program. One section per task, newest state
first. Update on every task state change.

## Status board (2026-07-06 early AM)

| Task | State | Where |
|---|---|---|
| Task 2 (Kimi K2 migration) | CLOSED - 2d gate FAILED 8/10 on the 90s chat_stream deadline; prod rolled back to Scout and confirmed green. Config fix merged (PR #145). Decision on K2 (raise deadline / streaming / park) with Chadi. | K2_QUALITY_SAMPLES.md, memory |
| Task 3 (RAG audit V2) | DONE - RAG_AUDIT_V2.md on main (afcb768). Headline: GK notes outrank fresh uploads 9/12. | RAG_AUDIT_V2.md |
| Task 4 (CI revival) | IN FLIGHT - PR #146: security-scan fix + timeouts + cancel-in-progress + pytest-timeout. First run expected to FAIL loudly at the hanging test (test_sandbox_block.py::test_execute_javascript_simple suspected - spawns node, runs only on Linux runners). Fix lands in same PR once the stack names the line. | PR #146, branch worktree-ci-revival |
| Task 5 (streaming on K2) | BLOCKED on Task 2 decision | - |
| TASK G (feature matrix sweep) | STARTED - registry extraction agent running; manifest next; sweep in paced batches against prod after H. | this file, tests/feature_matrix_manifest.yaml (pending) |
| TASK H (GK contamination knobs) | STARTED - implementation agent running in isolated worktree (branch feat/gk-contamination-knobs); evals will run against a LOCAL instance to protect prod. | RAG_AUDIT_V3.md (pending) |

## Standing rules in force

- Everything in TASK G goes through the normal chat path (fork_cli -> chat/stream -> smart_orchestrator). No direct block calls, no /v1/execute, no test hooks. A feature that only works via bypass is a routing FAIL.
- Do NOT rebuild/tune the keyword router now - log routing misses as evidence only.
- TASK H: no production default changes; Chadi picks from the V3 table.
- Prod protection: SYNTHESIS_STREAMING=0, LLM_PROVIDER=groq (Scout) + Ollama fallback. Do not flip without a deployed-smoke gate.
- No emojis anywhere in the repo. No secrets echoed in command lines.
- PARK after 3 failed attempts on the same error.
- Never delete projects (RAG chunks CASCADE). Eval project bc812f36 must stay.

## Key operational facts

- Prod: srv-d8hdc6ek1jcs739rq5sg (Render, workspace tea-d2gv3pf5r7bs73fh82eg), auto-deploys from main.
- Burst /v1/rag/search calls can health-check-kill the single-worker box - pace 3s+, back off on 5xx.
- Groq free tier 429s frequently; fallback (glm-5.2:cloud via Ollama tunnel) carries load. A throttled sweep run is not evidence - park if degraded.
- gh CLI installed + authed (bopoadz-del) on this machine. Python 3.11.9 reinstalled at the pyvenv.cfg path; use The_Fork/.venv.
- CI zombie runs: 2 of mine cancelled; 6 older ones (cloudflare bot branch + pre-existing) left running - Chadi may cancel from the Actions UI.

## 2026-07-06 log

- PR #146 opened (CI revival). Tests run 28755289990 in flight with pytest-timeout instrumentation; monitor armed.
- Agent launched: orchestrator action-registry extraction (TASK G-1 input).
- Agent launched: TASK H knobs implementation (isolated worktree, defaults-off, intent exemption).
- TASK C findings: the 3 zero-chunk projects (bb00878f, df28d3c0, e483b574) NO LONGER EXIST on prod
  (removed in the 2026-06-29 junk purge) - C1 closes as resolved-by-purge. C3 (zero-chunk WARNING
  log at index time) still valuable, small - queued. C2 (471-file Drive drain) depends on the Drive
  connector, disconnected on prod - PARK pending Chadi (also a G3-adjacent infra decision).
- TASK G-1 registry extracted from code: 53 distinct actions verified (docstring "52" and config
  "39" are stale); ~11 actions are hint-only labels; routing gate = select_agent_for_message,
  kill-switch SMART_ORCH_ROUTING_DISABLED; evidence events: route/tool_call/tool_result.
  Manifest authored: tests/feature_matrix_manifest.yaml on branch feat/feature-matrix-sweep
  (13 must-cover features x 2 phrasings + full coverage set, 3 mechanical oracles).
- Agent launched: sweep runner build (scripts/feature_matrix_sweep.py) on feat/feature-matrix-sweep.
- TASK H implementation landed on feat/gk-contamination-knobs (3501f8b): RAG_GK_SCORE_MARGIN /
  RAG_OWN_DOC_BOOST / RAG_GK_TOPK_CAP, all default-off; intent exemption (calc/standards/knowledge
  bypass); 11/11 new tests. FINDING: test_doc_search_api::test_search_returns_ranked_results is
  red on UNMODIFIED main - GK curated notes now crowd uploaded docs entirely out of top-5 (the
  audit symptom, worse). Pre-existing, affects CI green - to be quarantined-with-reason in PR #146
  and tracked by TASK H.
- Agent launched: TASK H config-grid sweep (8 configs x 3 harnesses) against a LOCAL instance;
  deliverable RAG_AUDIT_V3.md.
- CI run 28755289990: security scan PASSES on all 3 jobs (the __import__ fix verified in CI);
  jobs running the full suite with --timeout=120 armed.
- CI diagnostic run COMPLETE: all 3 jobs finished in ~21 min (vs 6h hangs). pytest-timeout
  CONVICTED the hang: test_sandbox_block::test_execute_javascript_simple, Timeout >120s with
  the main thread parked inside block.process (node subprocess pipeline). Suite reality on
  main: 40 failed + 15 collection errors + 1561 passed - regressions accumulated while CI was
  dead. Sandbox fixed (024daf4): one outer deadline around spawn+communicate in JS and bash
  paths - a sandbox that can hang its caller is not a sandbox. Triage agent launched on the
  remaining families (chat contracts, orchestrator routing, e2e benchmarks, collection errors).
- TASK C3 shipped on fix/zero-chunk-warning (fa5b91c): ZERO_CHUNK warning marker + 2 tests.
  C1 resolved-by-purge, C2 parked (Drive connector disconnected).
- TASK G: runner built by agent (3fe6dfd, 9/9 contract tests, 68-prompt plan). Fixture project
  ff905e29 created on prod via normal API; sample_office.ifc + project_programme.xer +
  generated ground_floor_plan.dxf uploaded via the normal document path; manifest wired
  (a076d7c). SWEEP LAUNCHED against prod, 20s pacing, kill-safe resume via state file.
- TASK H sweep agent running (8 local configs x 3 harnesses).
