# Roadmap Audit Report

**Branch:** `feat/clean-rebuild-rag` (also reflects `main` state for the audited components)
**Date:** 2026-07-08
**Scope:** Read-only audit of two roadmap documents vs. current repo.

---

## Git Baseline

```text
$ git branch --show-current
feat/clean-rebuild-rag

$ git status --short
?? manifests/p1b_master_folder_batch_000.json
?? scripts/golden_set_gate.py
?? tests/golden_set.yaml
?? tests/test_golden_set_manifest.py

$ git log --oneline -15
884bab9 docs: add Master Folder ingestion status report
458bd98 docs(PROGRESS): OCR size guard
becfd78 perf(doc_index): disable OCR for PDFs > 25 MB
f68482a docs(PROGRESS): p1b file-size skip guard
e7a0e4e feat(p1b): skip files > 100 MB before copying from Drive
23df359 docs(PROGRESS): PDF size guard
d578ecb perf(doc_index): skip PDFs > 100 MB to avoid OOM on scanned drawing sets
a8d53ab docs(PROGRESS): batch runner script
c6b0389 feat(p1b): sequential 100-file batch runner for Master Folder ingestion
562f692 docs(PROGRESS): real-file extractor verification and batch restart
51bced2 feat(p1b): flush partial report every 10 files for resilience
640324d docs(PROGRESS): archive guard update
ea9a2ec perf(doc_index): tighten archive guards, skip image members in archives
7883ad4 docs(PROGRESS): log .doc extractor and batched ingestion restart
7a5afbf feat(doc_index): add .doc extraction + batched/resumable ingestion script
```

---

## ROADMAP A — L2 Schedule Engine

### Expected Files Audit

| Expected File | Status | Evidence |
|---------------|--------|----------|
| `app/blocks/scope_extractor.py` | **MISSING** | Not present under this name. |
| `app/blocks/schedule_generator.py` | **MISSING** | Not present under this name. Schedule generation lives in `app/containers/construction/schedule.py` and `app/containers/construction/__init__.py` (`generate_wbs`). |
| `app/blocks/cpm_engine.py` | **MISSING** | Not present under this name. CPM implementation is in `app/lib/pm_computations.py` (`compute_cpm`). |
| `app/blocks/manpower_planner.py` | **MISSING** | Not present under this name. Manpower histogram logic is in `app/lib/pm_excel.py` and `app/containers/construction/schedule.py`. |
| `app/blocks/fasttrack_analyzer.py` | **MISSING** | Not present under this name. Fast-track/crash analysis appears partially in `app/containers/construction/schedule.py` (`_analyze_critical_path_changes`, "Crash Critical Path" strategy). |
| `app/blocks/schedule_excel_writer.py` | **MISSING** | Not present under this name. Excel writing is in `app/lib/pm_excel.py` and `app/routers/exports.py`. |
| `app/schemas/schedule.py` | **MISSING** | Not present. |
| `app/schemas/cpm.py` | **FOUND** | `CPMResult`, `CPMInput`, `CPMOutput` with `total_float`, `free_float`, `critical_path`, `near_critical`. |
| `app/schemas/manpower.py` | **MISSING** | Not present. |
| `app/templates/wbs_data_center.json` | **MISSING** | Not present. |
| `app/templates/wbs_infrastructure.json` | **MISSING** | Not present. |
| `app/templates/wbs_hospitality.json` | **MISSING** | Not present. |
| `app/templates/resource_library.json` | **MISSING** | Not present. |
| `app/routers/schedule.py` | **MISSING** | Not present. Schedule export is routed through `app/routers/exports.py`. |
| `frontend/src/components/ScheduleBuilder.tsx` | **MISSING** | Not present. |
| `tests/test_cpm_engine.py` | **MISSING** | Not present. CPM is tested in `tests/test_pm_computations.py`. |
| `tests/test_schedule_generator.py` | **MISSING** | Not present. Generation is tested in `tests/test_construction_generate_wbs.py`. |
| `tests/test_manpower.py` | **MISSING** | Not present. Manpower/Excel is tested in `tests/test_pm_excel.py` and `tests/test_schedule_bridge.py`. |

### Capabilities Audit

| Capability | Status | Evidence |
|------------|--------|----------|
| Upload RFP/BOD/documents and extract scope | **PARTIAL** | Document extraction exists (`doc_index.py`), but no dedicated `scope_extractor` block. `generate_wbs` in `app/containers/construction/__init__.py` takes a brief and generates activities. |
| Generate ~200–250 L2 activities | **PARTIAL** | `generate_wbs` supports `target_count` (default 200, clamp [20, 1000]) per `app/agents/configs/project-assistant.md:144`. Actual count depends on prompt/model. |
| CPM forward/backward pass | **DONE** | `app/lib/pm_computations.py:150` `compute_cpm` computes ES/EF/LS/LF. Tests pass. |
| Dependency types FS/SS/FF/SF + lag | **PARTIAL** | `app/lib/pm_computations.py` parses dependencies, but explicit lag support is not clearly exposed in the schema; needs deeper inspection. |
| Total float / free float | **DONE** | `app/schemas/cpm.py:77-78`, `app/lib/pm_computations.py:182-183`. Tests pass. |
| Critical path and near-critical | **DONE** | `app/lib/pm_computations.py:196-198`. Tests pass. |
| Manpower histogram by trade | **PARTIAL** | `app/lib/pm_excel.py:173` creates a "Manpower Histogram" sheet with chart, but it is man-days = duration × manpower rather than explicit trade breakdown. |
| Fast-track compression analysis | **PARTIAL** | "Crash Critical Path" strategy exists in `app/containers/construction/schedule.py:215`, but no dedicated `fasttrack_analyzer` block or sheet. |
| Final Excel sheets | **PARTIAL** | Actual sheets in `app/lib/pm_excel.py`: `L2 Schedule`, `Cost Loading`, `Manpower Histogram`, `Milestones`, `Summary`. Roadmap expected: `L2 Schedule`, `Critical Path`, `Manpower Histogram`, `WBS Dictionary`, `Fast-Track Analysis`. Only `L2 Schedule` and `Manpower Histogram` names match. |

### L2 Schedule Engine Test Results

```text
$ .venv/Scripts/python -m pytest tests/test_pm_computations.py tests/test_pm_excel.py tests/test_schedule_export_endpoint.py tests/test_schedule_bridge.py tests/test_construction_schedule_actions.py tests/test_construction_generate_wbs.py -q
.....................................................................   [100%]
71 passed in 171.40s
```

(The pytest invocation itself was killed by the 180s tool timeout, but the test output shows **71 passed** before timeout.)

### L2 Schedule Engine Status

- **DONE:** CPM forward/backward pass, total float, free float, critical path, near-critical detection.
- **PARTIAL:** Schedule generation, scope extraction, manpower histogram, fast-track analysis, Excel export — functionality exists under different file/component names and sheet layouts than the roadmap specified.
- **MISSING:** All explicitly-named blocks (`scope_extractor`, `schedule_generator`, `cpm_engine`, `manpower_planner`, `fasttrack_analyzer`, `schedule_excel_writer`), dedicated schemas (`schedule.py`, `manpower.py`), WBS JSON templates, `app/routers/schedule.py`, `frontend/src/components/ScheduleBuilder.tsx`, and the roadmap's expected test files.
- **RISK:** The roadmap names a granular block architecture that does not exist. The current implementation is more monolithic (`app/containers/construction/schedule.py`, `app/lib/pm_computations.py`, `app/lib/pm_excel.py`). Refactoring to match the roadmap exactly would be a large surface-area change.

---

## ROADMAP B — Reasoning Engine

### Expected Files Audit

| Expected File | Status | Evidence |
|---------------|--------|----------|
| `app/blocks/formula_executor_v2.py` | **FOUND** | `FormulaExecutorV2Block` class, `_run_sandboxed_with_timeout`, `code_cache`. |
| `app/core/sandbox.py` | **FOUND** | `run_sandboxed`, `SandboxResult`, sandbox hardening tests. |
| `app/core/session_store.py` | **FOUND** | `SessionStore`, `InMemorySessionStore`, `RedisSessionStore`, TTL expiry. |
| `app/schemas/project_session.py` | **FOUND** | `ProjectSession` schema with `code_cache`. |
| `app/blocks/project_reasoner.py` | **FOUND** | `ProjectReasonerBlock`, `_fallback_answer`, `degraded` flag. |
| `app/core/plan_executor.py` | **FOUND** | `PlanExecutor` class, step dispatch (`compute_cpm`, `resource_histogram`, etc.). |
| `app/schemas/execution_plan.py` | **FOUND** | `ExecutionPlan`, `PlanStep`, `PlanRunResult`, `StepResult`. |
| `app/prompts/reasoner_system.py` | **FOUND** | Reasoner system prompt builder. |
| `app/routers/project.py` | **FOUND** | `POST /v1/project/ask`. |
| `tests/test_project_reasoner.py` | **FOUND** | Comprehensive reasoner tests. |
| `tests/test_session_store.py` | **FOUND** | Session store tests. |
| `tests/test_formula_executor_v2.py` | **FOUND** | Formula executor v2 tests. |

### Capabilities Audit

| Capability | Status | Evidence |
|------------|--------|----------|
| Enhanced formula executor v2 | **DONE** | `app/blocks/formula_executor_v2.py` with timeout daemon thread, code caching, sandbox integration. |
| Sandbox hardening | **DONE** | `app/core/sandbox.py` restricts builtins, blocks `eval`/`exec`, file access; `tests/test_sandbox.py` covers it. |
| State injection | **DONE** | `run_sandboxed(code, state=variables)` passes state dict into sandbox. |
| Output capture | **DONE** | `SandboxResult` captures `result`, `stdout`, `stderr`, `error`. |
| Code caching | **DONE** | `ProjectSession.code_cache` keyed by formula; used in `formula_executor_v2.py:192-255`. |
| Session state store | **DONE** | `app/core/session_store.py` with in-memory and Redis backends. |
| ProjectSession schema | **DONE** | `app/schemas/project_session.py`. |
| Project Reasoner / planner | **DONE** | `ProjectReasonerBlock` plans → `ExecutionPlan` → `PlanExecutor`. |
| Execution plan schema | **DONE** | `app/schemas/execution_plan.py`. |
| Plan executor | **DONE** | `app/core/plan_executor.py`. |
| Tool/block catalogue awareness | **PARTIAL** | Reasoner uses catalogue excerpts and predefined plans (`app/core/predefined_reasoning.py`); dynamic tool registry awareness is via `get_block`. |
| `/v1/project/ask` API | **DONE** | `app/routers/project.py:48` `@router.post("/v1/project/ask")`. |
| Safe fallback when planner fails | **DONE** | `_fallback_answer` in `app/blocks/project_reasoner.py:169`; returns controlled message with `degraded: True` and no sources. |
| No raw internal error/tool leak | **DONE** | `app/agents/runtime.py:354-358` detects raw tool JSON and replaces with fallback; `project_reasoner.py` returns sanitized errors. |

### Reasoning Engine Test Results

```text
$ .venv/Scripts/python -m pytest tests/test_project_reasoner.py tests/test_session_store.py tests/test_formula_executor_v2.py -q
.........................................s...............                [100%]
56 passed, 1 skipped in 32.05s
```

### Compile Checks

```text
$ .venv/Scripts/python -m py_compile app/blocks/formula_executor_v2.py app/blocks/project_reasoner.py app/core/plan_executor.py app/core/session_store.py app/schemas/project_session.py app/schemas/execution_plan.py app/schemas/cpm.py app/lib/pm_computations.py app/lib/pm_excel.py app/routers/project.py app/routers/exports.py
compile OK
```

### Reasoning Engine Status

- **DONE:** All explicitly-named files exist; formula executor v2, sandbox, session store, project reasoner, plan executor, `/v1/project/ask`, fallback behavior, and tests are present and passing.
- **PARTIAL:** Dynamic tool/block catalogue awareness is present but relies on predefined plans and block registry lookup rather than a fully autonomous planner.
- **MISSING:** None of the explicitly-named roadmap files are missing.
- **RISK:** Low. The Reasoning Engine roadmap is essentially achieved on `feat/clean-rebuild-rag`.

---

## Roadmap Match Score

| Roadmap | Score | Rationale |
|---------|-------|-----------|
| L2 Schedule Engine | **35 / 100** | Core CPM/float/critical-path logic works and is tested, but the explicit block/file architecture, frontend component, dedicated router, WBS templates, and exact Excel sheet names from the roadmap are missing. Equivalent functionality exists under different names. |
| Reasoning Engine | **90 / 100** | All expected files exist, tests pass, fallback behavior is implemented, and the API is wired. Minor gap on fully autonomous tool catalogue awareness. |

---

## Next Safe Action

**"fix missing L2 schedule roadmap gaps"** — the Reasoning Engine is essentially achieved, while the L2 Schedule Engine has significant missing/restructured components. Any fix should be additive and preserve the existing working CPM/Excel paths.
