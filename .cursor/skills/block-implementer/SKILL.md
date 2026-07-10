---
name: block-implementer
description: Implement approved block specs (code, registry, tests, smoke). Hand redesign to architect. Not the in-app Fork Construction Agent.
---

# block-implementer (Cursor skill)

Thin pointer. **Source of truth:** `.agent-core/block-implementer.md` (manifest: `.agent-core/block-implementer.json`).

## When to use

- Approved `block-architect` spec (or explicit inline spec) ready to land
- Registry + pytest + `/v1/execute` smoke for a new/updated block

## When not to use

- Redesign / unclear contracts -> `block-architect`
- Construction-domain policy conflicts -> `construction-expert`

## Delegate

Read `.cursor/agents/block-implementer.md` or `.agent-core/block-implementer.md`.

Sync: `docs/agents/README.md`.
