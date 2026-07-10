# construction-expert audit (Phase 1)

Audit of **repository-visible** influences on the construction-expert / block-agent **development** contracts.  
Hidden Cursor/Claude system prompts and private platform wrappers are **NOT AVAILABLE** - not claimed.

Date context: Phase 1 agent-infrastructure pass. Branch recorded at implement time in the mission final report.

## KNOWN

Exact paths inspected or confirmed present:

### Development agent definitions (pre-existing)

- `.claude/agents/construction-expert.md` - full construction coding agent (frontmatter + body); **preserved and merged** into portable core
- `.claude/agents/block-architect.md` - design agent; preserved frontmatter examples
- `.claude/agents/block-implementer.md` - implement agent; preserved frontmatter examples
- `.claude/agents/*.md` - also present: `chain-debugger`, `code-reviewer`, `coder`, `coding-orchestrator`, `devops-engineer`, `docs-writer`, `fullstack-dev-advisor`, `general-purpose-assistant`, `security-auditor`, `test-runner`, `test-writer`

### Skills

- `.claude/skills/run-the-fork/SKILL.md`
- `.claude/skills/run-the-fork/driver.py`

### Construction runtime surfaces (inspect / skim only - not modified)

- `app/containers/construction/` - package (`__init__.py`, `boq.py`, `chat.py`, `documents.py`, `qto.py`, `schedule.py`, `helpers.py`); `ConstructionContainer`
- `app/blocks/smart_orchestrator.py` - multi-action keyword router; includes `health_check` keywords; docstring ~53-action / ~52 unique
- `app/blocks/` specialists (non-exhaustive): `boq_processor.py`, `bim.py`, `bim_extractor.py`, `drawing_qto.py`, `primavera_parser.py`, `spec_analyzer.py`, `sympy_reasoning.py`, `construction_v2.py`, `construction_advisor.py`, `document_engine.py`, `cache_manager.py`, `schedule_generator.py`, `project_reasoner.py`, …
- `app/core/plan_executor.py` - includes `render_artifact` step (expose deliverable; plans without it answer inline)
- `app/prompts/construction_expert.txt` - **APP runtime PMC prompt** (not the coding agent)
- `app/agents/configs/` - listed only (in-app agents), including e.g. `construction-pm.md`, `bim-analyst.md`, `contracts-manager.md`, `quantity-surveyor.md`, `smart-orchestrator-agent.md`, …
- `app/core/rag/inject.py` - project grounding mention (skim signal)
- `app/containers/construction/documents.py` - `_process_office_document`, fitz usage on PDF paths, anti-fabrication comments (skim)

### Tests (existence)

- `tests/test_predefined_steps.py` - present
- `tests/test_project_reasoner.py` - present

### Docs / plans mentioning honesty (skim signals)

- `docs/superpowers/plans/2026-05-21-construction-container-refactor.md` - no fabricated data
- In-app `app/agents/configs/construction-pm.md` - never fabricate data (product agent; distinct from coding agent)

### Activation methods visible in repo

- Claude Code: YAML `name` / `description` (with `<example>` blocks) / `model: inherit` / `memory: project` on `.claude/agents/*.md`
- Explicit handoff tables naming sibling agents
- Memory directory convention: `.claude/agent-memory/<agent>/` (referenced in agent files; commit of memory dumps not part of this mission)

### Tools / memory / handoffs / verification (only if visible)

From `.claude/agents/construction-expert.md` (pre-merge) and siblings:

- **Handoffs visible:** `coder`, `block-architect` -> `block-implementer`, `chain-debugger`, `security-auditor`
- **Memory path visible:** `.claude/agent-memory/construction-expert/`
- **Verification visible:** curl `/v1/execute` smoke with `construction` / `auto_pipeline`; panel shape checks; real `data/` fixtures; no synthetic data rules
- **Tools:** no exhaustive host tool ACL in repo; run-the-fork skill documents uvicorn/pytest/curl/browser smoke. Anything beyond that is **INFERRED** or **NOT AVAILABLE**

## CURSOR-NATIVE

- **Before Phase 1:** `.cursor/` directory was **not present** (NOT AVAILABLE as pre-existing Cursor agent pack in this clone).
- **Phase 1 added (repo-owned, Cursor-oriented):** `.cursor/agents/*.md`, optional `.cursor/skills/*/SKILL.md`.
- Cursor **hidden** system prompts / private routing: **NOT AVAILABLE**.

## CLAUDE-NATIVE

- `.claude/agents/*.md` frontmatter and bodies (KNOWN).
- `.claude/skills/run-the-fork/**` (KNOWN).
- `.claude/agent-memory/**` convention referenced (path convention KNOWN; contents not audited/committed here).
- Claude **hidden** system prompts / private auto-delegation internals: **NOT AVAILABLE**.

## REPO-OWNED (Phase 1 deliverables)

- `.agent-core/construction-expert.md` + `.json`
- `.agent-core/block-architect.md` + `.json`
- `.agent-core/block-implementer.md` + `.json`
- `.claude/agents/{construction-expert,block-architect,block-implementer}.md` (synced wrappers; frontmatter preserved/merged)
- `.cursor/agents/{construction-expert,block-architect,block-implementer}.md`
- `.cursor/skills/{construction-expert,block-architect,block-implementer}/SKILL.md` (optional thin pointers)
- `docs/agents/README.md`
- `docs/agents/construction-expert-audit.md` (this file)

## INFERRED

- Host IDEs will load `.claude/agents` / `.cursor/agents` using platform-specific discovery not fully documented in-repo.
- Task/subagent tool lists in some Cursor sessions may mirror these agent names; exact injection mechanism **not** visible in repo files.
- Older agent text referring to `app/containers/construction.py` (~5400 LOC) and `app/blocks/container_construction.py` described a **prior layout**; current KNOWN layout is the `app/containers/construction/` package. Line numbers in old notes are stale -> treat as search-for.

## NOT AVAILABLE

- `AGENTS.md` - missing
- `CLAUDE.md` - missing
- `.cursor/` prior to Phase 1 - missing
- `docs/agents/` prior to Phase 1 - missing
- `app/containers/construction.py` (monolith file) - missing (package exists instead)
- `app/blocks/container_construction.py` - missing
- `app/blocks/project_dashboard.py` - missing
- `app/core/workflow_templates.py` - missing
- `app/core/cm_step_aliases.py` - missing
- `app/core/cross_domain_reasoner.py` - missing
- `tests/test_cm_step_aliases.py` - missing
- `tests/test_cm_engine_wireup.py` - missing
- `tests/test_cm_orchestrator_enrichment.py` - missing
- `tests/test_project_dashboard.py` - missing
- Hidden Cursor system prompts
- Hidden Claude system prompts
- Private platform routing / proprietary wrappers
- Guaranteed include/import syntax for agent markdown across hosts (hence full-body sync)

## Separation reminder

| Surface | Role |
|---|---|
| Dev `construction-expert` | Coding subagent contracts in `.agent-core` / wrappers |
| `app/prompts/construction_expert.txt` | Runtime PMC prompt |
| `app/agents/configs/*` | In-app product agents |

Do not conflate these in audits or handoffs.
