# qa_qc_inspection — prompt 1, run 1

- verdict: PARTIAL
- prompt: prepare a QC inspection and punch list for the level 5 blockwork
- project: dar_al_arkan_master
- conversation_id: fmx-6ef2af9bac25
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "qa_qc_inspection", "confidence": 0.8, "reason": "below_routing_gate"}`
- tool_calls: [{"name": "construction", "args": "{\"input\":\"Prepare a comprehensive QC inspection and punch list for Level 5 blockwork. The blockwork falls under CESMM4 Class U (Brickwork, blockwork \\u0026 masonry), measured in m2. Include pre-pour/p", "id": null}]
- tool_results: [{"name": "construction", "ok": true, "duration_ms": 0, "error": null}]
- routing oracle: PASS
- execution oracle: PASS
- structure: {"has_checklist_items": "FAIL"}
- first_token_s: 54.22  total_s: 55.49
- served_model: glm-5.2:cloud  fallback_used: False
- answer_chars: 11743  sources: 3  events: 155

## Answer (verbatim)

# QC Inspection & Punch List — Level 5 Blockwork

**Measurement basis:** CESMM4 Class U — Brickwork, blockwork & masonry, primary unit m² *(source: CESMM4 work classes reference, chunk 2)*

---

## 1. Pre-Laying / Material Receipt Inspection

| # | Inspection Item | Acceptance Criterion | Method | Pass / Fail |
|---|---|---|---|---|
| 1.1 | Block units — type & grade | Matches approved shop drawing / material submittal | Visual + delivery note | ☐ P ☐ F |
| 1.2 | Block dimensions | Length × height × width as specified (typ. 400×200×100/150/200 mm) | Steel tape, sample 5 per pallet | ☐ P ☐ F |
| 1.3 | Block compressive strength | ≥ specified MPa (e.g. 5.0 N/mm² for non-load-bearing, 7.0+ for load-bearing) | Test certificate review | ☐ P ☐ F |
| 1.4 | Block moisture condition | Dry / protected from rain; not saturated at time of laying | Visual + moisture meter | ☐ P ☐ F |
| 1.5 | Block chipping / cracking | < 5 % of units with edge damage > 25 mm | Visual, random sample | ☐ P ☐ F |
| 1.6 | Mortar mix design | Cement : lime : sand ratio per spec (e.g. 1:1:6 or 1:½:4½) | Mix design submittal + site batch check | ☐ P ☐ F |
| 1.7 | Sand gradation | Conforms to BS EN 13139 / project spec | Sieve analysis report | ☐ P ☐ F |
| 1.8 | Reinforcement (if applicable) | Galvanised / stainless bed-joint reinforcement, correct diameter & spacing | Delivery note + visual | ☐ P ☐ F |
| 1.9 | Wall ties (if cavity wall) | Type, length, corrosion protection per spec | Visual + cert | ☐ P ☐ F |
| 1.10 | DPC membrane | Correct width, thickness ≥ spec, intact | Visual + thickness gauge | ☐ P ☐ F |
| 1.11 | Storage on Level 5 | Blocks on pallets / dunnage, protected from weather, stacked ≤ 1.5 m height | Visual | ☐ P ☐ F |

---

## 2. Setting Out & First Course Inspection (Hold Point)

| # | Inspection Item | Tolerance | Method | Pass / Fail |
|---|---|---|---|---|
| 2.1 | Wall alignment to grid lines | ± 10 mm from design position | Total station / laser + string line | ☐ P ☐ F |
| 2.2 | Wall thickness | ± 5 mm from nominal | Steel tape at 3 m intervals | ☐ P ☐ F |
| 2.3 | DPC installation | Full width, lapped min. 100 mm at joints, continuous | Visual | ☐ P ☐ F |
| 2.4 | First course levelness | ± 5 mm over 3 m run, ± 10 mm overall | Spirit level / laser level | ☐ P ☐ F |
| 2.5 | First course plumbness | ± 5 mm per storey height | Plumb bob / digital level | ☐ P ☐ F |
| 2.6 | Mortar bed thickness (first course) | 10 mm ± 3 mm | Steel tape at 5 locations | ☐ P ☐ F |
| 2.7 | Bond pattern established | Stretcher / English / Flemish bond per drawing, min. ⅓ lap | Visual | ☐ P ☐ F |
| 2.8 | Starter bars / cast-in dowels | Position, diameter, projection length per rebar drawing | Visual + tape | ☐ P ☐ F |
| 2.9 | Opening positions (doors, windows, ducts) | ± 10 mm from drawing dimension | Tape measure | ☐ P ☐ F |

**Sign-off:** Contractor QC ____ | Consultant ____ | Date: ____

---

## 3. In-Progress Blockwork Inspection (per lift / day)

| # | Inspection Item | Tolerance / Criterion | Method | Frequency | Pass / Fail |
|---|---|---|---|---|---|
| 3.1 | Plumbness (verticality) | ± 10 mm per storey height (≤ 3 m); ± 15 mm max | Plumb rule / laser | Every 2 m run | ☐ P ☐ F |
| 3.2 | Horizontal alignment (line) | ± 10 mm over 5 m run | String line / laser | Every course spot-check | ☐ P ☐ F |
| 3.3 | Levelness of coursing | ± 5 mm per 3 m run | Spirit level | Each lift | ☐ P ☐ F |
| 3.4 | Mortar joint thickness — bed joint | 10 mm ± 3 mm | Steel tape | 5 per wall per lift | ☐ P ☐ F |
| 3.5 | Mortar joint thickness — perpend (vertical) | 10 mm ± 3 mm, fully filled | Steel tape + visual | 5 per wall per lift | ☐ P ☐ F |
| 3.6 | Joint filling — no unfilled perpends | 100 % filled, no through-voids | Visual (flashlight) | Continuous | ☐ P ☐ F |
| 3.7 | Course height consistency | ± 2 mm per course from nominal | Storey rod / gauge | Every 5th course | ☐ P ☐ F |
| 3.8 | Bond / lap length | ≥ ¼ block length (min. 100 mm) | Visual + tape | Continuous | ☐ P ☐ F |
| 3.9 | Reinforcement placement (bed-joint type) | Correct course, lap, continuous, no kinks | Visual | Each reinforced course | ☐ P ☐ F |
| 3.10 | Wall ties (cavity / veneer) | 2.5 ties/m², staggered, min. 50 mm embed | Visual + count | Per m² | ☐ P ☐ F |
| 3.11 | Toothing / raking back | Racked back in steps ≤ 225 mm; no excessive toothing | Visual | Each lift break | ☐ P ☐ F |
| 3.12 | Cutting of blocks | Cut with masonry saw, not broken by hammer; min. piece ≥ 100 mm | Visual | Continuous | ☐ P ☐ F |
| 3.13 | Weather protection | Work covered with tarp / membrane at end of shift | Visual | Daily | ☐ P ☐ F |
| 3.14 | Mortar workability & retempering | Mortar used within initial set; retempering with water only within 2 h | Visual / slump | Per batch | ☐ P ☐ F |
| 3.15 | Movement / expansion joints | Position, width, filler material per drawing | Visual + tape | At each joint | ☐ P ☐ F |
| 3.16 | Service penetrations formed (not cut after) | Sleeves / ducts installed as work proceeds | Visual | At each penetration | ☐ P ☐ F |
| 3.17 | Lintels above openings | Correct type, bearing min. 150 mm each side, level | Visual + tape | Each opening | ☐ P ☐ F |
| 3.18 | Curing / protection of completed work | Keep mortar damp in hot/dry weather ≥ 3 days | Visual | Daily | ☐ P ☐ F |

---

## 4. Completion / Final Inspection (Hold Point)

| # | Inspection Item | Tolerance / Criterion | Method | Pass / Fail |
|---|---|---|---|---|
| 4.1 | Overall wall plumbness | ± 10 mm per storey; ± 15 mm max for Level 5 | Plumb laser / theodolite | ☐ P ☐ F |
| 4.2 | Overall wall alignment | ± 10 mm from grid line | Total station | ☐ P ☐ F |
| 4.3 | Wall thickness | ± 5 mm from nominal | Tape at 5 locations | ☐ P ☐ F |
| 4.4 | Level of top course | ± 5 mm from design level | Laser level | ☐ P ☐ F |
| 4.5 | Opening dimensions (doors, windows) | Width ± 10 mm, height ± 10 mm | Tape | ☐ P ☐ F |
| 4.6 | Opening sill level | ± 5 mm from design | Laser / level | ☐ P ☐ F |
| 4.7 | Lintel installation & bearing | Bearing ≥ 150 mm each side; level; plumb | Visual + tape | ☐ P ☐ F |
| 4.8 | Joint finish — flush / keyed for plaster | Per spec; uniform appearance | Visual + touch | ☐ P ☐ F |
| 4.9 | Mortar droppings / smears on face | Cleaned off; no staining | Visual | ☐ P ☐ F |
| 4.10 | Cavity cleanliness (if cavity wall) | No mortar bridges / debris in cavity | Visual (mirror / camera) | ☐ P ☐ F |
| 4.11 | DPC continuity | No tears, no punctures, lapped correctly | Visual | ☐ P ☐ F |
| 4.12 | Movement joints | Clean, filler intact, sealant ready (if applicable) | Visual | ☐ P ☐ F |
| 4.13 | Reinforcement continuity to next lift | Starter bars correct diameter, length, spacing | Visual + tape | ☐ P ☐ F |
| 4.14 | Wall ties — corrosion protection intact | No exposed bare steel | Visual | ☐ P ☐ F |
| 4.15 | Cracks | No cracks > 0.3 mm width | Visual + crack gauge | ☐ P ☐ F |
| 4.16 | Surface finish / texture | Uniform, no efflorescence, no spalling | Visual | ☐ P ☐ F |
| 4.17 | Interfaces with concrete columns / slabs | Tight joint, no gap > 5 mm; flexible filler where required | Visual | ☐ P ☐ F |
| 4.18 | Embedded items (sockets, boxes, sleeves) | Correct position, securely fixed, not loose | Visual + tape | ☐ P ☐ F |
| 4.19 | Cleaning of completed wall | Brushed down, no loose mortar, no efflorescence | Visual | ☐ P ☐ F |
| 4.20 | Dimensional check — overall wall length | ± 15 mm from drawing dimension | Tape / laser | ☐ P ☐ F |

**Sign-off:** Contractor QC ____ | Consultant ____ | Date: ____

---

## 5. Punch List — Level 5 Blockwork

### Severity Categories

| Code | Severity | Definition | Action Timeline |
|---|---|---|---|
| **A** | Critical | Structural integrity / safety risk; prevents progression of following trades | Rectify before any further work proceeds |
| **B** | Major | Non-conformance affecting performance, durability, or dimensional accuracy; does not block next trade but must be fixed | Rectify within 7 days |
| **C** | Minor | Cosmetic / minor tolerance deviation; no performance impact | Rectify before practical completion |

### Punch List Register

| Item No. | Location (grid / wall ref.) | Description of Defect | Severity (A/B/C) | Photo Ref. | Action Required | Responsible | Date Raised | Date Rectified | Verified By |
|---|---|---|---|---|---|---|---|---|---|
| PL-01 | | Wall out of plumb > tolerance | A / B | | Rebuild affected area to within ±10 mm | | | | |
| PL-02 | | Mortar joint thickness outside 10 ± 3 mm | B | | Rake out & repoint or rebuild affected courses | | | | |
| PL-03 | | Unfilled perpend (vertical joint) | B | | Fill with mortar, ensure full engagement | | | | |
| PL-04 | | Block cut too small (< 100 mm) at wall end / opening | B | | Replace with correctly cut block | | | | |
| PL-05 | | Coursing level deviation > 5 mm over 3 m | B | | Adjust subsequent courses / rebuild if severe | | | | |
| PL-06 | | Mortar smear / droppings on exposed face | C | | Clean with brush / water (no acid unless approved) | | | | |
| PL-07 | | Cavity bridged by mortar droppings | A | | Clean cavity, remove debris, ensure ties clear | | | | |
| PL-08 | | DPC torn / punctured / not lapped | A | | Replace DPC section, ensure 100 mm min. lap | | | | |
| PL-09 | | Wall tie missing or incorrectly spaced | B | | Install additional ties at 2.5/m², staggered | | | | |
| PL-10 | | Bed-joint reinforcement misplaced / wrong course | A | | Remove & reinstall at correct course | | | | |
| PL-11 | | Opening dimension out of tolerance (±10 mm) | B | | Cut back & reform or adjust frame | | | | |
| PL-12 | | Lintel bearing < 150 mm | A | | Install additional bearing / replace lintel | | | | |
| PL-13 | | Crack > 0.3 mm width | A / B | | Investigate cause; epoxy inject or rebuild | | | | |
| PL-14 | | Movement joint not formed at design location | B | | Cut joint to full depth, install filler | | | | |
| PL-15 | | Service penetration cut post-construction (not pre-formed) | B | | Record for structural assessment; seal annulus | | | | |
| PL-16 | | Efflorescence on surface | C | | Brush off; monitor; do not seal until dry | | | | |
| PL-17 | | Wall not keyed / raked back at break | B | | Rake back in steps ≤ 225 mm for continuation | | | | |
| PL-18 | | Excessive cutting with hammer (ragged edges) | C | | Replace with sawn units | | | | |
| PL-19 | | Interface gap with concrete column > 5 mm | B | | Fill with approved flexible filler / mortar | | | | |
| PL-20 | | Embedded box / socket loose or misaligned | C | | Refix securely; adjust position if within tolerance | | | | |

---

### Notes

1. **Hold Points** are at first course (Stage 2) and final completion (Stage 4). No work shall proceed beyond a hold point without signed approval from the Consultant / Engineer's representative.
2. **Witness Points** are in-progress inspections (Stage 3). The Contractor shall notify the Consultant 24 hours before the activity so inspection can be attended.
3. All blockwork on Level 5 is measured in **m²** per CESMM4 Class U. Deductions for openings > 0.1 m² shall apply per BOQ preamble.
4. Tolerances cited are standard industry values (BS EN 1996-2 / BS 8000-3). Where the project specification imposes tighter tolerances, the project spec governs.
5. Photographic records shall accompany every punch list item — minimum one close-up and one context photo per defect.

---

*This QC inspection and punch list was generated from standard blockwork inspection methodology. The CESMM4 classification (Class U, m²) is cited from the project reference corpus (chunk 2, source: CESMM4 work classes). Project-specific tolerances, material grades, and drawing references should be confirmed against the approved shop drawings and project specification for Level 5.*
