# UAE Construction Knowledge Base — MVP scaffold

> **Status: MVP scaffold. Full corpus pending operator review.**
> Only 3 demo entries are encoded so far (one per domain). The loader, evaluator,
> workflow validator, and 5-stage test gates are wired and passing. Once the
> operator approves these three entries' shape and provenance handling, the
> remaining ~60 entries from the verified file map will be added in the same
> schema.

## Domains

- `construction.buildings` — high-rise structures (Landmark Tower, Abu Dhabi)
- `construction.concrete` — mix design, mass-pour heat, high-strength, precast, RCC (cross-cuts buildings and roads)
- `construction.roads` — earthworks, haulage, compaction, geotech, pavements, tunnels (Dubai roads binder)
- `construction.procurement` — tendering / payments / change management (Dubai Parks and Resorts, Samsung C&T / Hill International)

## Schema (per entry)

```
id, domain, type (formula|threshold|rule|decision_pattern|checklist|reference_design|workflow),
title, statement, expression, variables{unit,desc}, thresholds,
applicability {applies_to[], region_specific, project_specific, standards_cross_ref[]},
remediation[],
provenance {source, project, confidence, verified_against_standard},
credibility_tier (1-5),
needs_review (bool)
```

For `type=workflow`: also `states[]`, `transitions[{from,to,guard}]`,
`required_documents[]`, `approval_roles[]`.

## Credibility tiers

| Tier | Meaning |
|---|---|
| 5 | Cross-checked vs cited standard + dimensionally valid |
| 4 | Dimensionally valid + consistent worked example (controlled documents) |
| 3 | Single-project site experience — DEFAULT |
| 2 | Ambiguous scan / needs_review |
| 1 | Unverified |

`evaluate()` and `validate_transition()` always surface a `"verify against your spec/geotech"`
warning when tier <= 2 OR `region_specific` is set OR `project_specific` is set.

## MVP entries

### 1. `thermal.equilibrium_time` (formula, `construction.buildings`)

Mass-concrete pour equilibrium time as a function of half-thickness.

- **Expression:** `168 * (X / 1.5)**2` (X in metres, result in hours)
- **Worked example:** X = 1.2 m → 107.52 hr
- **Provenance:** SMGTC552 part 2, Landmark Tower Abu Dhabi (CCC), site_experience
- **Credibility tier:** 3
- **Applicability:** UAE, project-specific to Landmark Tower

### 2. `earthworks.swelling_factor` (formula, `construction.roads`)

Swelling factor from Modified Proctor density, loose density, and compaction factor.

- **Expression:** `(A * C) / B` (A, B in t/m³; C as raw percent number)
- **Worked example:** Sub-base row A=2.18, B=1.6, C=96 → 130.8
- **Convention note:** The published table value is 1.31 — the published convention treats
  C as a decimal (0.96) while the formula uses C raw (96). The entry's `statement` field
  documents this; one of the test gates locks in the convention.
- **Provenance:** Message_from_MGTC55234 sect 2, Dubai roads/earthworks reference binder,
  site_experience, cross-referenced to AASHTO T 180-93 (Modified Proctor)
- **Credibility tier:** 3
- **Applicability:** UAE

### 3. `procurement.tender_lifecycle` (workflow, `construction.procurement`)

Dubai Parks and Resorts tender lifecycle state machine.

- **States:** `JOB_REQUISITION → SOLE_SOURCE_REVIEW? → RAT_REQUESTED → RAT_ISSUED → RFP_ISSUED → TENDER_QUERIES → TENDER_ANALYSIS → PREFERRED_TENDERER → BAFO → AWARDED`
- **Guard example:** `RAT_ISSUED → RFP_ISSUED` requires `context.rat_number is not None`
- **Required documents:** JR_TEM-601, SSJ_TEM-602, RAT, RFP_TEM-613, TenderEvaluationReport_TEM-624, AwardRecommendationLetter_TEM-625
- **Approval roles:** Estimator, VP_Project_Management, Contracts_Manager, Tender_Analysis_Committee, Project_Director
- **Provenance:** PRC-601 through PRC-604, Dubai Parks and Resorts (Samsung C&T / Hill International)
- **Credibility tier:** 4 (controlled documents)
- **Applicability:** UAE, project-specific

## Loader API

```python
from app.blocks._knowledge import (
    load_knowledge,        # list entries; optional domain filter
    get_rule,              # fetch one entry by id
    evaluate,              # run a formula entry against numeric inputs
    validate_transition,   # run a workflow entry against (state, event, context)
)
```

Returned dicts always include `provenance`, `credibility_tier`, and a `warnings` list
so the calling LLM never silently applies a UAE site prior to a project elsewhere.

## Safe guard parser

Workflow `guard` strings are author-controlled JSON but are still treated as untrusted
input. `_safe_guard_eval` parses each guard with `ast.parse(mode="eval")` and walks the
tree against a strict allowlist:

- `ast.Constant`, `ast.Name "context"`, `ast.Attribute` (only on `context`),
  `ast.Subscript` (only on `context`)
- `ast.Compare` with `Eq | NotEq | Lt | LtE | Gt | GtE | Is | IsNot | In | NotIn`
- `ast.BoolOp` (`And`, `Or`), `ast.UnaryOp` (`Not`)

Everything else — `ast.Call`, `ast.Lambda`, imports, free names, `ast.BinOp` arithmetic —
is rejected with `GuardEvalError` before any value is produced.

## Tests

`tests/test_construction_kb.py` — 28 cases covering the 5-stage gates per entry plus
loader-shape tests and a security suite for the guard parser. All passing.
