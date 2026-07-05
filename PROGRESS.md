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
