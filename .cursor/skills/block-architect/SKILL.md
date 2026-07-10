---
name: block-architect
description: Design Cerebrum blocks and chains (contracts, registry, tests). Does not implement by default. Not the in-app Fork Construction Agent.
---

# block-architect (Cursor skill)

Thin pointer. **Source of truth:** `.agent-core/block-architect.md` (manifest: `.agent-core/block-architect.json`).

## When to use

- New block or chain design
- Unclear I/O / registry / panel contracts before code

## When not to use

- Clear construction bug on an existing action -> `construction-expert`
- Spec already approved and user wants code -> `block-implementer`

## Delegate

Read `.cursor/agents/block-architect.md` or `.agent-core/block-architect.md`.

Sync: `docs/agents/README.md`.
