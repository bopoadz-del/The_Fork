# Formula Verification Table — additive library

One row per additive formula for the operator's one-by-one sign-off. Each row:
the exact inputs, the hand-derived oracle value, the governing clause, and the
oracle test that locks it. All values are reproduced in the test comments so you
can check them against the standard without running code.

Status legend: **GATED** = implemented + oracle test green + in the coverage
guard. Sign-off column is for the operator (`OK` / notes).

## Batch 1 — Structural steel (AISC 360-16 / EN 1993) + Loads (ASCE 7-16 / EN 1991/1998)

Module: `app/lib/construction_formulas_structural_steel.py`, `_loads.py`
Tests: `tests/test_formula_structural_steel.py`, `tests/test_formula_loads.py`

| Formula | Code | Inputs | Oracle | Clause | Status | Sign-off |
|---------|------|--------|--------|--------|--------|----------|
| steel_tension_capacity | AISC | Ag=3000, Ae=2550 mm², Fy=345, Fu=450 | rupture governs → **860.63 kN** (yield 931.5) | AISC 360-16 §D2 | GATED | |
| steel_tension_capacity | EC | A=3000, Anet=2550, fy=355, fu=490 | rupture → **899.64 kN** (yield 1065) | EN 1993-1-1 §6.2.3 | GATED | |
| bolt_shear_capacity | AISC | Ab=380 mm², Fnv=372, 1 plane | **106.02 kN** (0.75·372·380) | AISC 360-16 §J3.6 | GATED | |
| bolt_shear_capacity | EC | A=303 mm², fub=800, αv=0.6, 1 plane | **116.35 kN** (0.6·800·303/1.25) | EN 1993-1-8 Tbl 3.4 | GATED | |
| weld_capacity | AISC | leg=6, L=100 mm, FEXX=490 | throat 4.242 → **93.54 kN** | AISC 360-16 §J2.4 | GATED | |
| weld_capacity | EC | leg=6, L=100, fu=490, βw=0.9 | fvw,d 251.5 → **106.67 kN** | EN 1993-1-8 §4.5.3.3 | GATED | |
| wind_pressure | ASCE | V=50 m/s, Kz=1, Kzt=1, Kd=0.85 | **1302.6 Pa** (0.613·0.85·50²) | ASCE 7-16 §26.10 | GATED | |
| wind_pressure | EC | vb=50, ρ=1.25 | **1562.5 Pa** (0.5·1.25·50²) | EN 1991-1-4 §4.5 | GATED | |
| seismic_base_shear | ASCE | W=10000 kN, SDS=1.0, R=8, Ie=1 | Cs=0.125 → **1250 kN** | ASCE 7-16 §12.8.1 | GATED | |
| seismic_base_shear | EC | W=10000, ag/g=0.3, S=1.2, q=3.9, λ=0.85 | Cs=0.1962 → **1961.5 kN** | EN 1998-1 §4.3.3.2 | GATED | |
| live_load_reduction | ASCE | L0=4.79 kN/m², KLL=4, AT=40 m² | factor 0.611 → **2.928 kN/m²** | ASCE 7-16 §4.7.2 | GATED | |
| live_load_reduction | EC | L0=4.79, ψ0=0.7, A=40 | αA=0.75 → **3.593 kN/m²** | EN 1991-1-1 §6.3.1.2 | GATED | |

## Batch 2 — Quantities (geometry, code-agnostic)

Module: `app/lib/construction_formulas_quantities.py`
Tests: `tests/test_formula_quantities_earthwork.py`

| Formula | Inputs | Oracle | Basis | Status | Sign-off |
|---------|--------|--------|-------|--------|----------|
| concrete_volume (rect) | 10×5×0.3 m, 5% waste | net **15.0**, +waste **15.75** m³ | geometry | GATED | |
| concrete_volume (cyl) | D=0.6, H=10 m | **2.827 m³** (π·0.09·10) | geometry | GATED | |
| concrete_volume (trap) | top2/bot4/dep1.5/L10 | net **45.0**, +waste **47.25** m³ | geometry | GATED | |
| rebar_weight | d16, 12 m, ×50 | unit **1.578 kg/m** → **947.0 kg** | BS 8666 | GATED | |
| rebar_by_area | 20 m², 200 mm, d12 | 5 bars/m → **88.8 kg** | geometry | GATED | |

## Batch 3 — Earthwork / geotech (code-agnostic)

Module: `app/lib/construction_formulas_earthwork.py`
Tests: `tests/test_formula_quantities_earthwork.py`

| Formula | Inputs | Oracle | Basis | Status | Sign-off |
|---------|--------|--------|-------|--------|----------|
| excavation_volume | 20×10×3 m, 25% bulk | bank **600**, loose **750 m³** | geometry/bulking | GATED | |
| backfill_volume | excav 600, struct 200, 20% swell | void **400**, loose **480 m³** | geometry/swell | GATED | |
| cut_fill_balance | cut 5000, fill 3500 | surplus **1500** (export), haul **1875 m³** | mass balance | GATED | |
| compaction_control | field 1.90, MDD 2.00 | **95.0%** → PASS | ASTM D698/D1557 | GATED | |
| slope_fos_simple | φ=30°, β=20° | **1.586** (cohesionless); +c′ 5 kPa → **1.874** | infinite slope | GATED | |

## Pending batches (scoped, not yet built — see additive-formula-library-scope.md)

- Batch 4 — Structural RC (ACI 318 / EC2): beam_moment_simple, beam_moment_point_load, beam_moment_uniform, beam_shear_simple, slab_thickness_min, rebar_lap_length, masonry_wall_capacity
- Batch 5 — QC: concrete_cylinders, concrete_shrinkage, concrete_curing_time
- Batch 6 — Cost/PM/planning: roi_calculator, unit_cost_total, cost_per_sf, productivity_rate, critical_path_float
- Batch 7 — Safety (OSHA): scaffold_load_capacity, fall_arrest_force, crane_lift_capacity
- Batch 8 — Digital + reference-tables: prefab_module_weight, carbon_footprint_concrete, leed_points_estimate, bim_clash_tolerance, laser_scan_accuracy

Each batch: one module + oracle tests (dual-code where a code applies) + smoke
inputs, green through the full referee gate (`comprehensive_engine_test` 93/93 +
coverage guard + calc-intact) before merge.
