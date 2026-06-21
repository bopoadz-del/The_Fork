# Hermes Desktop skill spec — Diriyah training scenario generation

Operational draft for re-firing the Diriyah training-scenario generator as a
Hermes Agent skill, replacing the current WebBridge curl tunnel + admin API
poll loop.

## Stage 1 verification summary

All claims in the user's pitch were verified against raw primary sources
(GitHub API JSON, raw HTML from docs sites, raw HTTP HEAD on Ollama library
pages). See bottom of file for citations.

| Claim | Verdict | Evidence |
| --- | --- | --- |
| Persistent local process on Windows | REAL | Native installer at `hermes-agent.nousresearch.com/desktop` (HTTP 200, preloads `platform-art-windows.webp`); installer creates `%LOCALAPPDATA%\hermes` per docs |
| Parallel subagents on partitioned workloads | REAL | `delegate_task(tasks=[...])` tool, `delegation.max_concurrent_children` default 3, no hard ceiling — Subagent Delegation page |
| Self-improving skills that write/improve their own Python | REAL (with nuance) | Skills are primarily Markdown (SKILL.md) with optional `scripts/` for Python. The "self-improving" loop refers to the agent creating/refining SKILL.md procedures during TAO loops, not autonomously editing arbitrary Python at runtime. The skill files live under `~/.hermes/skills/` and the agent can modify/delete them |
| `ollama launch hermes-desktop` | REAL — exact verbatim command | `docs.ollama.com/integrations/hermes-desktop`: "Quick start: `ollama launch hermes-desktop`". Distinct from `ollama launch hermes` (CLI only) |
| `qwen3.6:27b` and `qwen3.6:27b-mlx` Ollama tags | REAL | `ollama.com/library/qwen3.6:27b` and `:27b-mlx` return HTTP 405 (page exists, just rejects HEAD); both listed in the official tag list. Note: Hermes's Ollama integration page recommends `qwen3.6` for local use (~24 GB VRAM); the `:27b` variant is the 17 GB / 256K-context build |
| `ollama launch` exists | REAL | Introduced in Ollama v0.15 (Jan 2026) |
| Hermes Agent GitHub repo | REAL | `github.com/NousResearch/hermes-agent`, repo ID 1024554267, public, releases v0.15.x and v0.16.0 published May–June 2026 |

Minor note: the existing The_Fork backend runner at
`app/routers/admin.py:218` (`_training_job_runner`) is referenced as the
behavioural contract this skill must reproduce.

## Why a skill instead of the current setup

Current shape (verified by reading `app/routers/admin.py:304-353`):

- `POST /v1/admin/training/generate-scenarios?project_id=...` returns a
  `job_id` immediately.
- A background `asyncio` task in the Render-hosted FastAPI process runs
  `_training_job_runner`, calling `iter_chunks_for_project`,
  `_generate_for_chunk`, then `_validate_scenarios` from
  `scripts/generate_training_scenarios.py`.
- The client polls `GET /v1/admin/training/job/{job_id}` until status is
  `done` or `failed`.

Failure modes today:

- The WebBridge curl tunnel is ephemeral and dies on the operator PC's
  reboot (per memory: `the-fork-ollama-cloud.md`).
- Long polls over flaky tunnels drop. The runner survives, but the
  operator's visibility into it does not.
- Chunk-by-chunk generation is serialised even though chunks are
  independent.

A Hermes skill running on the operator's local Windows machine:

- Persists across the tunnel — Hermes is a local long-running process.
- Can fan out `_generate_for_chunk` across `delegate_task(tasks=[...])`
  subagents, each with its own conversation, so model context isn't
  bottlenecked.
- Owns the polling loop locally — no client-side disconnect risk.

## Install prerequisites

Quoted verbatim from primary docs. **Verify each step on the operator
machine before relying on it** — the spec was written from docs, not from
a local install.

1. Install Ollama (already done on operator PC per existing setup).
2. Install Hermes Desktop:
   - Easiest: `ollama launch hermes-desktop` — Ollama detects missing
     install and runs the Nous installer.
   - Alternative (Windows native, no Ollama dependency):
     `iex (irm https://hermes-agent.nousresearch.com/install.ps1)` then
     `hermes desktop`.
3. Pull the recommended local model:
   `ollama pull qwen3.6:27b` (or `qwen3.6:27b-mlx` on Apple silicon).
4. Confirm `~/.hermes/skills/` exists. On Windows this is
   `%USERPROFILE%\.hermes\skills\` (or `%LOCALAPPDATA%\hermes\skills\`
   depending on installer mode — **verify on first run; the docs describe
   both layouts and the operator install determines which applies**).

## Skill layout

Path: `%USERPROFILE%\.hermes\skills\thefork-diriyah-training\`

```
thefork-diriyah-training/
  SKILL.md                  # frontmatter + procedure (mandatory)
  scripts/
    fire_training_job.py    # thin wrapper around the existing admin API
    poll_job.py             # polling helper with backoff
  references/
    admin-api.md            # copy of the admin endpoint contract
    generate-scenarios.md   # signature notes for generate_training_scenarios
```

The `SKILL.md` frontmatter follows the format documented at
`hermes-agent.nousresearch.com/docs/user-guide/features/skills`:

```yaml
---
name: thefork-diriyah-training
description: |
  Re-fire the Diriyah (or any The_Fork project) training-scenario
  generation job against the deployed The_Fork backend, partition chunks
  across parallel subagents when needed, and stream progress until the
  output JSONL is on disk.
version: 0.1.0
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [thefork, training, diriyah]
    category: thefork
requires_toolsets: [terminal, web]
---
```

Slash invocation (per Skills System page — "Every installed skill is
automatically available as a slash command"):

```
/thefork-diriyah-training project_id=diriyah questions_per_chunk=3 max_chunks=200
```

## Procedure (the Markdown body of SKILL.md)

Hermes runs this as instructions to itself; it is not Python that
executes directly. The actual side-effects happen via `scripts/*.py` and
the agent's terminal toolset.

1. **Resolve job parameters.** Defaults: `project_id=diriyah`,
   `questions_per_chunk=3`, `min_chunk_chars=150`, `max_chunks=200`,
   `provider_hint=any`.
2. **Decide partitioning.** If `max_chunks > 60`, partition into N
   sub-ranges of equal size where N = `min(ceil(max_chunks / 60), 3)`
   (3 is the default delegation cap per
   `delegation.max_concurrent_children`). Otherwise run as a single job.
3. **Single-job path.** Run `python scripts/fire_training_job.py
   --project-id <id> --questions-per-chunk <q> --min-chunk-chars <m>
   --max-chunks <M> --provider-hint <p>`. The script POSTs to
   `https://the-fork.onrender.com/v1/admin/training/generate-scenarios`
   with the configured admin API key (read from
   `%USERPROFILE%\.thefork-backup\.env` per memory entry
   `the-fork-secrets-backup.md`) and returns the `job_id` on stdout.
4. **Partitioned path.** Call `delegate_task` with one task per
   sub-range. Each subagent runs the single-job path with its
   sub-range's `max_chunks` slice (note: the current admin API does
   not take an offset, so the chunk-slicing has to happen on the
   `scripts/generate_training_scenarios.py` side — flagged as a
   follow-up; see "Open questions" below).
5. **Poll.** For each `job_id`, run `python scripts/poll_job.py
   --job-id <id> --interval 30`. The poller hits
   `GET /v1/admin/training/job/{job_id}`, prints the `chunks_done`
   counter on each tick, and exits 0 on `status == "done"`, 1 on
   `failed`, 2 on 404 (uvicorn restart — re-fire).
6. **Collect outputs.** Each job writes
   `data/learning/training_scenarios_<project_id>_<unix>.jsonl` on the
   server. The skill instructs Hermes to either (a) fetch each output
   via a separate `/v1/admin/training/output/<job_id>` endpoint
   **(does not exist today — see follow-up)** or (b) SSH to the Render
   container, which isn't supported on the starter plan. For now the
   skill stops at job completion and prints the server-side path.

## Self-improvement hooks

Per the Skills System page, Hermes can edit any skill under
`~/.hermes/skills/`. The expected self-improvement loop for this skill:

- After each run, the agent appends a `## Pitfalls` entry to `SKILL.md`
  if it hit any of: timeout, 404 on poll, `chunks_skipped > 30% of
  total_chunks`, validation rejection rate above a threshold.
- The "skills meta-tool" referenced by the docs creates new SKILL.md
  files; it does **not** autonomously rewrite The_Fork's Python source.
  Any change to `_generate_for_chunk`'s prompt would still be a normal
  pull request to The_Fork — flagged in case the user's pitch implied
  otherwise.

## Open questions / unverified items

- The `delegate_task` parallel path assumes the server-side generator
  supports chunk offsets. It currently does not (see
  `iter_chunks_for_project` in `scripts/generate_training_scenarios.py`,
  which takes only `min_chars` and `max_chunks`). Either add an offset
  param to that function and the admin endpoint, or keep the skill in
  single-job mode until that lands.
- The output-fetch step needs an admin endpoint that streams the
  resulting JSONL back to the client. Not built today.
- The Hermes "skill meta-tool" name and exact signature for creating
  skills mid-conversation was not captured from a primary-source page
  during stage 1. The Skills System page documents the SKILL.md format
  and `skills_list` / `skill_view` access pattern, but the *creation*
  meta-tool wasn't quoted verbatim. Treat self-improvement step above
  as the design intent, not a documented Hermes API.
- The `qwen3.6:27b` build (17 GB) is sufficient for the chunk-level Q&A
  generation prompt in `_DEFAULT_PROMPT` based on prompt length, but the
  spec hasn't been benchmarked. Run a 10-chunk smoke test before
  committing to it.

## Primary sources (raw, not summarised)

- `gh api repos/NousResearch/hermes-agent` — repo exists, ID 1024554267,
  public, description "The agent that grows with you".
- `gh api repos/NousResearch/hermes-agent/releases` — v0.16.0 (2026-06-06),
  v0.15.2 (2026-05-29), v0.15.1 (2026-05-29) with `.whl` and `.tar.gz`
  assets.
- `https://hermes-agent.nousresearch.com/docs/user-guide/features/skills`
  — raw HTML fetched, gave verbatim SKILL.md frontmatter format,
  `~/.hermes/skills/` location, slash-command invocation, progressive
  disclosure (`skills_list`, `skill_view`).
- `https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation`
  — verbatim `delegate_task` signature, `tasks=[...]` batch shape,
  `delegation.max_concurrent_children` (default 3, no hard ceiling),
  ThreadPoolExecutor batching, result ordering by task index.
- `https://hermes-agent.nousresearch.com/docs/getting-started/installation`
  — Windows install via PowerShell `iex (irm ...install.ps1)`,
  `hermes desktop` launches the GUI after CLI install,
  `%LOCALAPPDATA%\hermes` / `~/.hermes/` layout.
- `https://docs.ollama.com/integrations/hermes-desktop` — verbatim
  `ollama launch hermes-desktop` Quick Start, `--model <model>` flag.
- `https://docs.ollama.com/integrations/hermes` — verbatim
  `ollama launch hermes` for CLI flow; recommended local models
  include `qwen3.6` (~24 GB VRAM).
- `https://ollama.com/library/qwen3.6` (HEAD) — HTTP 405 (page
  exists); tag list confirmed `qwen3.6:27b`, `qwen3.6:27b-mlx`,
  `qwen3.6:35b`, `qwen3.6:35b-mlx`, `qwen3.6:latest`.

## In-repo references this spec is built against

- `app/routers/admin.py:218` — `_training_job_runner` background task.
- `app/routers/admin.py:304-353` — POST endpoint that kicks off the job.
- `app/routers/admin.py:357` — GET endpoint for poll status.
- `scripts/generate_training_scenarios.py:85` — `iter_chunks_for_project`.
- `scripts/generate_training_scenarios.py:119` — `_generate_for_chunk`.
- `scripts/generate_training_scenarios.py:297` — `_validate_scenarios`.
