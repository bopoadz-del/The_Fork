# Development agents (portable contracts)

Repo-owned agent infrastructure for **coding/development** subagents - not product features, not the in-app Fork Construction Agent, not deployment.

## Agents

| Agent | Does | Does not |
|---|---|---|
| **construction-expert** | Construction-domain coding: BOQ/QTO/BIM/schedule/procurement/RFI/NCR/claims/payment/risk, orchestrator honesty, panel contracts, project grounding | Act as the in-app PMC/chat Construction Agent; design unrelated greenfield blocks by default; deploy |
| **block-architect** | Design block/chain contracts, schemas, registry/UI/security impact, acceptance + test plans | Implement by default; invent synthetic success paths; own construction-domain bugfixes that belong on existing actions |
| **block-implementer** | Implement approved specs: `app/blocks/`, registry, tests, targeted smoke, security scan | Silent redesign; fake data to pass smoke; commit/push unless asked |

## How the three differ

- **construction-expert** - domain reasoning + construction-specific implementation boundaries.
- **block-architect** - design/contracts/chains only (until explicitly asked to implement).
- **block-implementer** - code/tests after the design is clear; hands redesign back to architect.

Handoffs are documented in each `.agent-core/*.md` and mirrored in the JSON manifests.

## Source of truth and portability

| Layer | Path | Role |
|---|---|---|
| **Canonical** | `.agent-core/<id>.md` + `.agent-core/<id>.json` | Vendor-neutral source of truth + machine-readable manifest (`version` 1.0.0 for Phase 1) |
| **Claude Code** | `.claude/agents/<id>.md` | Platform frontmatter (`name`, `description` with examples, `model`, `memory`) + **full synced body** from `.agent-core` |
| **Cursor** | `.cursor/agents/<id>.md` | Same sync strategy; optional thin skills under `.cursor/skills/<id>/SKILL.md` |
| **Docs** | `docs/agents/` | README + audit |

Claude Code does **not** reliably support markdown includes across agent files in this repo's observed layout, so wrappers use a **full synchronized body** with a `SYNCED FROM .agent-core/...` header - not a thin `@include`.

### Sync process (`.agent-core` ↔ `.claude/agents` ↔ `.cursor/agents`)

1. Edit **only** `.agent-core/<id>.md` (and update `.agent-core/<id>.json` if activation/paths/handoffs change).
2. Re-copy the body into `.claude/agents/<id>.md` and `.cursor/agents/<id>.md`, preserving each file's YAML frontmatter (especially Claude `description` examples used for auto-delegation).
3. Keep the HTML comment: `<!-- SYNCED FROM .agent-core/<id>.md ... -->` immediately after frontmatter.
4. Optionally refresh `.cursor/skills/<id>/SKILL.md` pointers (skills stay short; they do not fork rules).
5. Spot-check with:

```powershell
Select-String -Path .agent-core\*,.claude\agents\*,.cursor\agents\* -Pattern "not the in-app Fork Construction Agent" -SimpleMatch
Select-String -Path .agent-core\*,.claude\agents\*,.cursor\agents\* -Pattern "No synthetic"
```

Do **not** commit `.claude/agent-memory/**` or secrets.

## Invoke: Claude Code vs Cursor

### Claude Code

- Agents under `.claude/agents/` with YAML frontmatter are the repo-visible activation surface.
- Descriptions/examples drive auto-delegation when the host supports it.
- Project memory paths (when enabled): `.claude/agent-memory/<id>/`.
- Related skill for running the app: `.claude/skills/run-the-fork/`.

### Cursor

- Agents under `.cursor/agents/` (synced bodies).
- Optional skills under `.cursor/skills/<id>/SKILL.md` for "when to delegate" hints.
- Host Task/subagent routing may also list these names; **hidden Cursor system prompts are NOT AVAILABLE** in-repo - do not invent them.

## Portable vs platform-specific

- **Portable:** `.agent-core` markdown + JSON (purpose, paths, handoffs, verification, failure policy).
- **Platform-specific:** Claude/Cursor frontmatter, memory directories, host auto-delegation, any proprietary routing (not visible here).

## Later map to CerebrumDev.ai

JSON manifests (`.agent-core/*.json`) are intentionally vendor-neutral (`id`, `version`, `activation`, `allowed_paths`, `handoffs`, `verification`, …) so a future CerebrumDev.ai catalog can import them without scraping platform wrappers. Phase 1 does **not** deploy or wire that catalog.

## Differs from the future / current in-app Fork Construction Agent

| | Dev `construction-expert` | In-app Construction / PMC agents |
|---|---|---|
| Audience | Engineers changing the repo | End users in the product chat |
| Files | `.agent-core`, `.claude/agents`, `.cursor/agents` | `app/agents/configs/*`, `app/agents/runtime.py`, `app/prompts/construction_expert.txt` |
| Job | Implement & protect domain code | Answer project questions via runtime tools/blocks |
| Synthetic data | Forbidden in code paths | Forbidden in answers (product honesty) - separate surface |

`app/prompts/construction_expert.txt` is the **APP runtime PMC prompt**. It is **not** this coding agent. Inspect-only unless the task explicitly targets product prompts.

## Related audit

See [construction-expert-audit.md](./construction-expert-audit.md) for KNOWN / NOT AVAILABLE inventory of what influenced Phase 1.
