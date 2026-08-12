# Construction capability audit — 2026-08-12

Every construction capability was put against three bars. A capability passes
only if it clears all three.

| Bar | Question | How it was answered |
|---|---|---|
| 1 | **Not a stub.** Does it compute, or does it return a shaped constant? | Read the body; run it on a real input; look for placeholder values and empty successes. |
| 2 | **Current version.** Is this the live implementation, or a stale twin the router forgot? | Compare against same-named / similar-named siblings; check what the caller surfaces actually name; check whether tests validate against production or against their own copy of it. |
| 3 | **Really tested.** Would the suite notice if it broke? | **Control-delete**: replace the whole body with a shape-only return, run the tests that claim to cover it, and require RED. Restore, require GREEN. |

Bar 3 is the one that finds things. A capability can be real, current and
completely unprotected — and in this codebase, most of the gaps were exactly
that shape.

---

## Results

| Capability | Bar 1 | Bar 2 | Bar 3 | Outcome |
|---|---|---|---|---|
| BOQ | pass | pass | **FAIL** | `boq_process` gutted → tests green. Strengthened with payload assertions. |
| Documents | pass | pass | **FAIL** | `process_document` gutted → **148 passed**. Net-new payload tests. |
| Schedule — `parse_primavera_schedule` | pass | pass | pass | 2 RED on a real `.xer`. |
| Schedule — `generate_wbs` | pass | pass | pass | 14 RED. |
| Schedule — `resource_histogram` | pass | pass | pass | 7 RED. |
| Schedule — `forensic_delay_analysis` | pass | pass | **FAIL** | Gutted → 16 tests still green. Net-new payload tests. |
| Cross-cutting — router surface | **FAIL** | **FAIL** | **FAIL** | 3 unreachable actions; 2 of them unrunnable; 1 false-green test; 1 placeholder value. |
| QTO | pass | pass | *pending* | Clean delegation to `DrawingQTOBlock`. |
| Chat | pass | pass | *pending* | Clean delegation. |
| Formulas / orchestration | *pending* | *pending* | *pending* | 14 actions. |

The strongest capability in the container is `parse_primavera_schedule`, and
the reason is instructive: it is the one with a **real file** behind its tests.
`tests/test_real_project_fixtures.py` asserts `project_duration == 459`,
`data_date == "2013-11-27"` and exactly 19 critical-path activities against a
1.6 MB Primavera baseline. Every capability that failed bar 3 was tested only
through routing or synthetic shape assertions. That correlation held for every
capability examined, and it is the best available predictor for the ones still
pending.

---

## What bar 3 actually caught

### `boq_process` — a status check cannot see an empty workbook

`tests/test_synthetic_fixture_actions.py` dispatched the action and asserted
`status == "success"`. Replacing the body with
`{"status": "success", "items": [], "total": 0}` kept it green. The action
could have stopped reading the file entirely.

Now asserts the line-item count and the arithmetic total off the synthetic
workbook.

### `process_document` — the entry point for twelve actions, zero payload coverage

Gutted to a shape-only return, **148 tests passed**. Every existing reference
was a string in a routing table, an entry in a feature manifest, or an
anti-hijack guard that wires `object()` in its place. Those prove the *router*
reaches it. None proved it reads a file.

`tests/test_process_document_payload.py` asserts `C32/40` — a value that can
only appear in the output if the specification was opened and parsed.

### `forensic_delay_analysis` — the expensive one to be wrong about

Gutted → 16 tests still green. Every reference in `tests/` was a string in an
alias list, an entry in the eval-battery harness, or a routing docstring. The
one test with `delay_analysis` in its name calls the *parser* and asserts
`isinstance(..., list)`.

This is the EOT / prolongation path: its output feeds a claim value and an
expert report. A version silently returning zeros would have produced a
confidently empty claim — understating entitlement, which is the expensive
direction.

The new tests are anchored on a **metamorphic property** rather than a golden
number: a programme analysed against *itself* must show zero net delay and an
unchanged critical path. That holds whichever baseline is used, so it does not
rot when fixtures change, and a stub cannot reach zero honestly without parsing
both files and differencing them. The reconciliation test is the same shape:
the compensable / non-compensable and excusable / non-excusable buckets must
*sum to the input count*, not merely exist.

---

## The cross-cutting failure: the router surface

This is where the audit found real defects rather than missing tests.

### Three actions the router could not dispatch

`generate_construction_report`, `extract_measurements` and `track_progress`
were fully-written public actions with no entry in the handlers table. One was
user-facing: `_suggest_next_action` recommends `generate_construction_report`
**by name** after a QA/QC run with defects, and dispatching that suggestion
returned `"Unknown action"`. The platform recommended something it then
refused to run.

### Wiring them was the wrong fix for two of the three

The first attempt routed all three. That was wrong, and the reason matters:
**unreachable code is often unreachable because it does not work.**

    generate_construction_report -> self._generate_doc_recommendations
    track_progress               -> self._assess_delay_risk
      (via _compare_photo_to_bim -> self._element_similarity,
                                 -> self._find_deviations)

None of those four helpers has ever been defined in this repository.
`git log -S "def <helper>" --all` returns nothing for all four.
`generate_construction_report` additionally reads five keys off
`process_document` that its current contract does not return — it was written
against an older version of that function.

Measured through `route()`:

| Action | Result |
|---|---|
| `track_progress` | `AttributeError: _assess_delay_risk` |
| `track_progress` (with photos) | `AttributeError: _find_deviations` |
| `generate_construction_report` | `KeyError: 'doc_type'` |
| `extract_measurements` | `{"status": "success", ...}` — runs |

So routing them converted an honest refusal into an unhandled 500. Only
`extract_measurements` stayed wired. The other two are parked, unrouted, and
registered — `docs/archive/HANDOFF.md` had parked them as *"real, need
multi-file resolution"*, which is wrong on both counts and is corrected in
`KNOWN_INCOMPLETE.md`.

They were **not deleted**. Defining the four helpers is domain logic to design
— an element similarity metric, a delay-risk heuristic, a deviation
classifier, a document recommendation generator — not wiring to restore.
Inventing them to make a test pass would be fabricating capability.

`extract_measurements` is not a twin of `extract_quantities`, which was the
other possibility worth ruling out: it reads a drawing and produces
measurements, while `extract_quantities` consumes a measurements *list*. They
are consecutive pipeline stages.

### The fence, and proof that it bites

`tests/test_construction_actions_reachable.py` closes three directions:

- **FORWARD** — every name a dispatch surface emits (`_suggest_next_action`,
  `auto_pipeline` `next_actions[]`, `cm_step_aliases.STEP_TO_TARGET`) is a
  handlers **key**, and `route()` really accepts it.
- **REVERSE** — every handlers key is named by some caller surface, asserted as
  **equality** against a documented externally-called set. A new orphan fails,
  *and* an entry that stops being an orphan fails. An allowlist would rot
  silently in one direction; equality rots in neither.
- **RUNNABLE** — no method calls a `self.<helper>` that does not exist,
  followed **transitively** up the call graph, plus one rule with no
  exceptions: *parking broken code is allowed, routing it is not.*

Verified by breaking the container five ways. Each turned the fence RED; each
restore returned it to GREEN:

| Mutation | Test that fired |
|---|---|
| un-wire a working action | `every_public_action_is_routable` + reverse reachability |
| route a parked broken action | `no_unrunnable_method_is_routed` |
| add a 5th phantom helper to a *working* method | `no_new_method_calls_a_helper_that_does_not_exist` |
| define one of the four phantom helpers | `no_new_method_calls_a_helper_that_does_not_exist` |
| add an orphan action nothing can name | `every_dispatchable_action_is_named_by_some_surface` |

Note the fourth row: defining `_assess_delay_risk` did **not** un-park
`track_progress`, because it still depends on two more phantom helpers one
level down. The transitive closure is load-bearing, not decoration.

Two namespaces matter throughout. The handlers table aliases on purpose
(`"schedule_risk": self.analyze_schedule_risk`), so the **keys** are what
callers name and the `self.<method>` targets are what the class defines.
Checks that mix them report false positives — a mistake made once during this
audit, and now called out in the test file so it is not repeated.

### A false-green machine (bar 2)

`tests/test_cm_step_aliases.py` validated `STEP_TO_TARGET` against
`_KNOWN_CONSTRUCTION_ACTIONS` — a hand-maintained literal set of thirty route
keys. It was checking the alias table against its own copy of the router
rather than against the router.

Proven, not asserted: deleting `"schedule_risk": self.analyze_schedule_risk`
from the handlers table leaves `STEP_TO_TARGET["recovery_options"] ->
("construction", "schedule_risk")` dangling, and that file stayed **GREEN**.
It is now derived from the handlers table and goes RED on the same mutation.

### A placeholder value (bar 1)

`extract_measurements` returned
`result.get("confidence", {}).get("measurement_extraction", 0)`.
`measurement_extraction` is computed nowhere — that line is its only occurrence
in the codebase — so every successful extraction reported **confidence 0**. A
reader cannot distinguish "the extractor is not sure" from "nobody computes
this", and the first reading is the one a user acts on.

Now `None`, with an assertion that a bare `0` cannot come back.

---

## Bar 1 sweep — flattering defaults (found, NOT fixed)

The `confidence` defect has a detectable signature: `.get("key", <literal>)`
where `"key"` is never *produced* anywhere. Run across the container package
that pattern hits 59 times. Most are legitimate — they read caller-supplied
input, and a default is the right behaviour there.

The dangerous subclass is where the default is a **flattering** value, so the
absence of data reads as a good result. Two of those, both confirmed by
grepping for a producer across all of `app/`:

**`procurement_optimizer` scores every supplier identically, from nothing.**
`boq.py:1787-1792` defaults six supplier scores to `70 / 75 / 80 / 80 / 60 /
70`. Nothing in `app/` produces `price_score`, `delivery_score`,
`quality_score`, `financial_score`, `esg_score` or `support_score`. Unless an
API caller hand-supplies them, every supplier scores exactly **73.8**, and the
action then presents a weighted ranking and a recommended supplier off numbers
nobody measured. A tie is not the visible outcome — a confident ordering is.

**`digital_twin_sync` can never report incomplete geometry.**
`_generate_sync_recommendations` (`__init__.py:1745`) tests
`quality.get("completeness_score", 100) < 80`. `completeness_score` is produced
nowhere, so the default is the only value the expression can take and the "Add
missing geometry to incomplete elements" recommendation is unreachable. The
sync report always reads as geometrically clean.

Neither is fixed here. Both are behaviour changes to a scoring model rather
than test or wiring defects, and picking replacement semantics — refuse,
return `None`, or label the scores as assumed — is a product decision, not an
audit one. Registering rather than guessing.

---

## Stated limitations

**`extract_measurements` extracts counts, not dimensions.** On the real drawing
fixture all 34 measurements come back as `type: "count"`, `unit: "ea"`. No
lengths, areas or volumes are recovered from a construction drawing. This is
consistent with the previously-registered drawing-precision finding and is a
drawing-reader problem, not a ranking or wiring one. The action is real and
useful for schedules of items; it is not a quantity take-off.

**`ACTION_HINTS` contains five entries no producer emits** —
`design_review_workflow`, `handover_management`, `inspection_request`,
`ncr_management`, `rfi_management` do not appear in `smart_orchestrator`. They
are inert (unmatched hints fall through to plain chat), so they are not fenced,
but they are dead vocabulary.

**The archive was not rewritten.** `docs/archive/HANDOFF.md` and
`PROGRESS.md` still carry the incorrect "need multi-file resolution" reason.
They are historical records; `KNOWN_INCOMPLETE.md` names and corrects them
rather than editing history.

---

## Still pending

- QTO and Chat — bar 3 control-deletes.
- Formulas / orchestration — all three bars across 14 actions.
- Bar 1 lying-return sweep per capability (the `confidence` finding above came
  out of this pass on one action; it has not been run across the rest).
- Real-file validation for BOQ, QTO and drawings, matching the standard
  `parse_primavera_schedule` already meets.
