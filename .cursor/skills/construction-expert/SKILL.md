---
name: construction-expert
description: Construction-domain coding specialist for The Fork. Use for BOQ/QTO/BIM/schedule/procurement/claims/payment/risk code changes. Not the in-app Fork Construction Agent.
---

# construction-expert (Cursor skill)

Thin pointer. **Source of truth:** `.agent-core/construction-expert.md` (manifest: `.agent-core/construction-expert.json`).

## When to use

- Any construction-domain implementation or safety review in this repo
- Protecting no-synthetic-data, orchestrator integrity, `render_artifact`/deliver honesty, office≠fitz, panel contracts, project grounding

## When not to use

- In-app PMC / chat agent persona (`app/agents/configs`, `app/prompts/construction_expert.txt`)
- Greenfield block design -> `block-architect`
- Implementing an approved new-block spec -> `block-implementer`

## Delegate

Read `.cursor/agents/construction-expert.md` or `.agent-core/construction-expert.md` and follow handoffs there.

Sync: edit `.agent-core` first, then wrappers - see `docs/agents/README.md`.
