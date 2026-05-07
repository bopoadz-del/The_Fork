"""
5-Stage Validation Block
Stage 1 → Schema      : required fields, types, units present
Stage 2 → Domain      : physical plausibility (positive qty, valid grades, unit ranges)
Stage 3 → Cross-Ref   : BOQ ↔ drawing ↔ spec internal consistency
Stage 4 → Benchmark   : costs within ±3σ of historical benchmarks
Stage 5 → Consistency : totals reconcile, no duplicates, logic holds
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from app.core.universal_base import UniversalBlock


# ── Domain rules ──────────────────────────────────────────────────────────────
_CONCRETE_GRADES = {"C20","C25","C30","C35","C40","C45","C50","C55","C60","C70","C80"}
_REBAR_DIAMETERS_MM = {6,8,10,12,16,20,25,32,40}
_VALID_UNITS = {"m","m2","m3","kg","t","no","pcs","lm","ls","sum","hr","day","wk","m²","m³"}
_POSITIVE_FIELDS = {"quantity","unit_cost","rate","total_cost","area_m2","volume_m3","weight_kg","length_m"}
_BENCHMARK_SIGMA = 3.0   # flag if outside 3σ

class ValidatorBlock(UniversalBlock):
    name = "validator"
    version = "1.0.0"
    description = "5-stage construction data validation: schema → domain → cross-ref → benchmark → consistency"
    layer = 3
    tags = ["reasoning", "validation", "construction", "qa", "pipeline"]
    requires = ["historical_benchmark"]

    default_config = {
        "run_stages": [1, 2, 3, 4, 5],
        "fail_fast": False,
        "benchmark_sigma": _BENCHMARK_SIGMA,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"boq_data":[...],"drawing_data":{},"spec_data":{},"historical_benchmarks":{}}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "overall_pass", "type": "boolean", "label": "Pass"},
                {"name": "stage_results", "type": "json", "label": "Stage Results"},
                {"name": "issues", "type": "list", "label": "Issues"},
                {"name": "credibility_score", "type": "number", "label": "Credibility Score"},
            ],
        },
        "quick_actions": [
            {"icon": "✅", "label": "Full Validation", "prompt": "Run all 5 validation stages"},
            {"icon": "🔍", "label": "Schema Check", "prompt": "Check schema and required fields only"},
            {"icon": "📊", "label": "Benchmark Check", "prompt": "Validate costs against benchmarks"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        boq_data          = data.get("boq_data", [])
        drawing_data      = data.get("drawing_data", {})
        spec_data         = data.get("spec_data", {})
        benchmarks        = data.get("historical_benchmarks", {})
        run_stages        = params.get("run_stages", self.config.get("run_stages", [1,2,3,4,5]))
        fail_fast         = params.get("fail_fast", self.config.get("fail_fast", False))
        sigma             = float(params.get("benchmark_sigma", self.config.get("benchmark_sigma", _BENCHMARK_SIGMA)))

        stage_results: Dict[int, Dict] = {}
        all_issues: List[Dict] = []
        stages_passed = 0

        stage_fns = {
            1: self._stage1_schema,
            2: self._stage2_domain,
            3: self._stage3_crossref,
            4: self._stage4_benchmark,
            5: self._stage5_consistency,
        }

        for stage_num in sorted(run_stages):
            fn = stage_fns.get(stage_num)
            if not fn:
                continue
            result = fn(boq_data, drawing_data, spec_data, benchmarks, sigma)
            stage_results[stage_num] = result
            all_issues.extend(result.get("issues", []))
            if result["pass"]:
                stages_passed += 1
            elif fail_fast:
                break

        total_stages = len(run_stages)
        overall_pass = stages_passed == total_stages
        credibility_score = round((stages_passed / max(total_stages, 1)) * 100, 1)

        return {
            "status": "success",
            "overall_pass": overall_pass,
            "stages_passed": stages_passed,
            "total_stages": total_stages,
            "credibility_score": credibility_score,
            "stage_results": stage_results,
            "issues": all_issues,
            "issue_count": len(all_issues),
            "critical_issues": [i for i in all_issues if i.get("severity") == "critical"],
        }

    # ── Stage 1: Schema ────────────────────────────────────────────────────────

    def _stage1_schema(self, boq, drawing, spec, benchmarks, sigma) -> Dict:
        issues = []
        required_item_fields = {"description", "quantity"}

        for idx, item in enumerate(boq):
            for field in required_item_fields:
                if field not in item or item[field] is None:
                    issues.append({
                        "stage": 1, "severity": "critical",
                        "code": "MISSING_FIELD",
                        "message": f"BOQ item [{idx}] missing required field '{field}'",
                        "item": item.get("description", f"item_{idx}"),
                    })
            # Unit must be recognisable
            unit = str(item.get("unit", "")).lower().strip()
            if unit and unit not in _VALID_UNITS:
                issues.append({
                    "stage": 1, "severity": "warning",
                    "code": "UNKNOWN_UNIT",
                    "message": f"BOQ item '{item.get('description','')}' has unrecognised unit '{unit}'",
                    "item": item.get("description", ""),
                })

        return {"stage": 1, "name": "Schema", "pass": not any(i["severity"]=="critical" for i in issues), "issues": issues}

    # ── Stage 2: Domain rules ──────────────────────────────────────────────────

    def _stage2_domain(self, boq, drawing, spec, benchmarks, sigma) -> Dict:
        issues = []

        for item in boq:
            desc = item.get("description", "")
            # Positive quantities
            for field in _POSITIVE_FIELDS:
                val = item.get(field)
                if val is not None:
                    try:
                        if float(val) < 0:
                            issues.append({
                                "stage": 2, "severity": "critical",
                                "code": "NEGATIVE_VALUE",
                                "message": f"'{desc}' has negative {field}: {val}",
                                "item": desc,
                            })
                    except (TypeError, ValueError):
                        issues.append({
                            "stage": 2, "severity": "warning",
                            "code": "NON_NUMERIC",
                            "message": f"'{desc}' field '{field}' is not numeric: {val}",
                            "item": desc,
                        })

            # Concrete grade plausibility
            if "concrete" in desc.lower():
                grade_match = re.search(r'\bC(\d+)\b', desc, re.IGNORECASE)
                if grade_match:
                    grade = f"C{grade_match.group(1)}"
                    if grade not in _CONCRETE_GRADES:
                        issues.append({
                            "stage": 2, "severity": "warning",
                            "code": "UNUSUAL_GRADE",
                            "message": f"Unusual concrete grade '{grade}' in '{desc}'",
                            "item": desc,
                        })

            # Rebar diameter plausibility
            if any(w in desc.lower() for w in ("rebar","reinforcement","bar")):
                diam_match = re.search(r'\b(\d+)\s*mm\b', desc)
                if diam_match:
                    diam = int(diam_match.group(1))
                    if diam not in _REBAR_DIAMETERS_MM:
                        issues.append({
                            "stage": 2, "severity": "info",
                            "code": "UNUSUAL_DIAMETER",
                            "message": f"Unusual rebar diameter {diam}mm in '{desc}'",
                            "item": desc,
                        })

        return {"stage": 2, "name": "Domain", "pass": not any(i["severity"]=="critical" for i in issues), "issues": issues}

    # ── Stage 3: Cross-reference ───────────────────────────────────────────────

    def _stage3_crossref(self, boq, drawing, spec, benchmarks, sigma) -> Dict:
        issues = []

        # BOQ ↔ drawing area check
        drawing_area = drawing.get("total_area_m2") or drawing.get("floor_area_m2")
        if drawing_area:
            for item in boq:
                desc = str(item.get("description","")).lower()
                if any(w in desc for w in ("slab","floor","screed","topping")) and item.get("unit","").lower() in ("m2","m²"):
                    qty = _to_float(item.get("quantity",0))
                    ratio = qty / float(drawing_area)
                    if ratio > 3.0:
                        issues.append({
                            "stage": 3, "severity": "warning",
                            "code": "AREA_OVERRUN",
                            "message": f"'{item.get('description','')}' qty {qty:.1f}m² is {ratio:.1f}× drawing area {drawing_area:.1f}m²",
                            "item": item.get("description",""),
                        })

        # BOQ ↔ spec grade cross-check
        spec_grades = spec.get("grade_requirements", [])
        spec_grade_values = {g.get("value","").upper() for g in spec_grades if g.get("type") == "concrete_strength"}
        for item in boq:
            desc = str(item.get("description",""))
            grade_match = re.search(r'\bC(\d+)\b', desc, re.IGNORECASE)
            if grade_match and spec_grade_values:
                grade = f"C{grade_match.group(1)}"
                if grade not in spec_grade_values and not any(grade in s for s in spec_grade_values):
                    issues.append({
                        "stage": 3, "severity": "warning",
                        "code": "GRADE_MISMATCH",
                        "message": f"BOQ grade '{grade}' not found in spec grades {spec_grade_values}",
                        "item": desc,
                    })

        return {"stage": 3, "name": "Cross-Reference", "pass": not any(i["severity"]=="critical" for i in issues), "issues": issues}

    # ── Stage 4: Benchmark ─────────────────────────────────────────────────────

    def _stage4_benchmark(self, boq, drawing, spec, benchmarks, sigma) -> Dict:
        issues = []

        for item in boq:
            key = item.get("item_key") or item.get("description","").lower().replace(" ","_")
            bench = benchmarks.get(key, {})
            avg = _to_float(bench.get("avg_cost", 0))
            std = _to_float(bench.get("std_dev", 0))
            actual = _to_float(item.get("unit_cost") or item.get("rate", 0))

            if avg <= 0 or actual <= 0:
                continue

            z = abs(actual - avg) / std if std > 0 else 0
            if z > sigma:
                direction = "above" if actual > avg else "below"
                issues.append({
                    "stage": 4, "severity": "critical" if z > sigma * 1.5 else "warning",
                    "code": "BENCHMARK_OUTLIER",
                    "message": (
                        f"'{item.get('description',key)}' rate {actual:.2f} is {z:.1f}σ {direction} "
                        f"benchmark avg {avg:.2f} (±{std:.2f})"
                    ),
                    "item": key,
                    "z_score": round(z, 2),
                })

        return {"stage": 4, "name": "Benchmark", "pass": not any(i["severity"]=="critical" for i in issues), "issues": issues}

    # ── Stage 5: Consistency ───────────────────────────────────────────────────

    def _stage5_consistency(self, boq, drawing, spec, benchmarks, sigma) -> Dict:
        issues = []

        # Total = sum of line totals
        grand_total_field = None
        line_sum = 0.0
        stated_total = None

        for item in boq:
            desc = str(item.get("description","")).lower()
            if any(w in desc for w in ("grand total","total cost","project total","contract sum")):
                stated_total = _to_float(item.get("total_cost") or item.get("rate",0))
                continue
            line_sum += _to_float(item.get("total_cost",0))

        if stated_total and stated_total > 0 and line_sum > 0:
            diff_pct = abs(stated_total - line_sum) / stated_total * 100
            if diff_pct > 2.0:
                issues.append({
                    "stage": 5, "severity": "critical",
                    "code": "TOTAL_MISMATCH",
                    "message": f"Stated grand total {stated_total:,.2f} differs from line sum {line_sum:,.2f} by {diff_pct:.1f}%",
                    "item": "grand_total",
                })

        # Duplicate item keys
        keys_seen: Dict[str, int] = {}
        for item in boq:
            key = item.get("item_key") or item.get("description","")
            keys_seen[key] = keys_seen.get(key,0) + 1
        for key, count in keys_seen.items():
            if count > 1:
                issues.append({
                    "stage": 5, "severity": "warning",
                    "code": "DUPLICATE_ITEM",
                    "message": f"Item '{key}' appears {count} times in BOQ",
                    "item": key,
                })

        # Zero-cost items
        zero_cost = [i.get("description","") for i in boq if _to_float(i.get("total_cost",0)) == 0 and _to_float(i.get("quantity",0)) > 0]
        for desc in zero_cost[:5]:
            issues.append({
                "stage": 5, "severity": "info",
                "code": "ZERO_COST",
                "message": f"Item '{desc}' has quantity > 0 but zero total cost",
                "item": desc,
            })

        return {"stage": 5, "name": "Consistency", "pass": not any(i["severity"]=="critical" for i in issues), "issues": issues}


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",","").strip())
    except (ValueError, TypeError):
        return 0.0
