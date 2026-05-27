"""Learning Engine Block - Tier promotion + coefficient tuning via scikit-learn"""

import os
import json
import time
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock

_STORAGE_PATH = os.environ.get("LEARNING_ENGINE_STORAGE", "/tmp/cerebrum_learning_engine.json")

# Tier thresholds: (min_executions, max_mae_pct) → tier label
_TIER_RULES = [
    (0,   "bronze"),   # new formulas start here
    (10,  "silver"),   # enough samples to train
    (50,  "gold"),     # good convergence
    (200, "platinum"), # high confidence
]


class LearningEngineBlock(UniversalBlock):
    name = "learning_engine"
    version = "1.0.0"
    description = "Tier promotion + coefficient tuning: learns from user corrections to improve formula accuracy"
    layer = 3
    tags = ["domain", "construction", "ml", "learning", "coefficients", "tier"]
    requires = []

    default_config = {
        "promotion_mae_threshold": 0.05,   # 5% MAE to promote
        "min_samples_for_training": 5,
        "storage_path": _STORAGE_PATH,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"correction_data": {"formula_id": "concrete_cost", "predicted": 125000, "actual": 118000}, "operation": "record_correction"}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "tier_level", "type": "text", "label": "Tier"},
                {"name": "updated_coefficients", "type": "json", "label": "Coefficients"},
                {"name": "promotion_flag", "type": "boolean", "label": "Promoted"},
            ],
        },
        "quick_actions": [
            {"icon": "📈", "label": "Check Tier", "prompt": "Show tier levels for all formulas"},
            {"icon": "🔧", "label": "Tune", "prompt": "Tune coefficients from latest corrections"},
            {"icon": "📊", "label": "Performance", "prompt": "Show model performance metrics"},
        ],
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self._state: Dict = self._load_state()

    def _load_state(self) -> Dict:
        path = self.config.get("storage_path", _STORAGE_PATH) if hasattr(self, "config") else _STORAGE_PATH
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"formulas": {}, "history": []}

    def _save_state(self):
        path = self.config.get("storage_path", _STORAGE_PATH)
        try:
            with open(path, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception:
            pass

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        # Default to "status" when called with bare text (no structured correction data)
        has_correction_data = data.get("formula_id") or data.get("correction_data") or params.get("formula_id")
        default_op = "record_correction" if has_correction_data else "status"
        operation = data.get("operation") or params.get("operation") or (data.get("text") or data.get("input") or "").strip() or default_op

        if operation == "record_correction":
            return await self._record_correction(data, params)
        elif operation == "tune":
            return await self._tune_coefficients(data, params)
        elif operation == "promote":
            return await self._evaluate_promotion(data, params)
        elif operation == "status":
            return self._get_status()
        elif operation == "reset":
            formula_id = data.get("formula_id") or params.get("formula_id")
            return self._reset_formula(formula_id)
        elif operation == "hydrate":
            # Nightly "sleep on it" pass — see app/core/learning/hydration.py.
            # Reads the day's conversations and files, indexes new docs,
            # writes recurring topics/friction back to the project_facts +
            # agent_facts the chat path consults, and records each friction
            # signal here as a pattern (durable across runs).
            from app.core.learning import hydration as _hydration

            return await _hydration.run(
                target_date=data.get("target_date") or params.get("target_date"),
                project_ids=data.get("project_ids") or params.get("project_ids"),
            )
        elif operation == "hydration_latest":
            from app.core.learning import hydration as _hydration

            return _hydration.get_latest(
                scope=data.get("scope") or params.get("scope") or "global",
                project_id=data.get("project_id") or params.get("project_id"),
            )
        elif operation == "hydration_history":
            from app.core.learning import hydration as _hydration

            return _hydration.list_history(
                scope=data.get("scope") or params.get("scope"),
                project_id=data.get("project_id") or params.get("project_id"),
                limit=int(data.get("limit") or params.get("limit") or 20),
            )
        elif operation == "record_pattern":
            return self._record_pattern(data, params)
        elif operation == "list_patterns":
            return self._list_patterns(data, params)
        else:
            return {
                "status": "error",
                "error": (
                    f"Unknown operation: {operation}. Use: record_correction, tune, "
                    "promote, status, reset, hydrate, hydration_latest, hydration_history, "
                    "record_pattern, list_patterns"
                ),
            }

    # ── Non-numeric observations (the hydration writeback target) ─────────
    #
    # `_record_correction` above is strictly for predicted-vs-actual numeric
    # tuning. The hydration pass produces a different kind of signal — "user
    # asked about rebar three times this week", "this project's chats keep
    # surfacing complaint language". These are categorical observations,
    # not regression samples, so they live in their own state slot.

    def _record_pattern(self, data: Dict, params: Dict) -> Dict:
        """Append one observation to the patterns corpus.

        Schema: ``_state["patterns"][project_id][category]`` is a list of
        ``{observation, source, run_date, ts}`` dicts. The corpus stays
        unbounded for now — hydration only adds a handful per run; if it
        ever grows beyond practical limits we can age it out by run_date.
        """
        project_id = data.get("project_id") or params.get("project_id")
        category = data.get("category") or params.get("category") or "general"
        observation = data.get("observation") or params.get("observation")
        if not project_id or not observation:
            return {"status": "error", "error": "project_id and observation required"}

        # Defensive init for state loaded from disk before this slot existed
        if "patterns" not in self._state:
            self._state["patterns"] = {}
        bucket = self._state["patterns"].setdefault(project_id, {}).setdefault(category, [])
        bucket.append({
            "observation": str(observation),
            "source": data.get("source") or params.get("source") or "manual",
            "run_date": data.get("run_date") or params.get("run_date"),
            "ts": time.time(),
        })
        self._save_state()
        return {
            "status": "success",
            "project_id": project_id,
            "category": category,
            "total_observations": len(bucket),
        }

    def _list_patterns(self, data: Dict, params: Dict) -> Dict:
        """Read the patterns corpus. Filter by project_id and/or category."""
        if "patterns" not in self._state:
            return {"status": "success", "patterns": {}, "count": 0}
        project_id = data.get("project_id") or params.get("project_id")
        category = data.get("category") or params.get("category")
        patterns = self._state["patterns"]
        if project_id:
            patterns = {project_id: patterns.get(project_id, {})}
        if category:
            patterns = {
                pid: {category: bucket.get(category, [])}
                for pid, bucket in patterns.items()
            }
        count = sum(
            len(items)
            for buckets in patterns.values()
            for items in buckets.values()
        )
        return {"status": "success", "patterns": patterns, "count": count}

    async def _record_correction(self, data: Dict, params: Dict) -> Dict:
        correction = data.get("correction_data", {})
        formula_id = correction.get("formula_id") or data.get("formula_id") or params.get("formula_id")
        predicted = correction.get("predicted") or data.get("predicted")
        actual = correction.get("actual") or data.get("actual")

        if not formula_id:
            return {"status": "error", "error": "formula_id required"}
        if predicted is None or actual is None:
            return {"status": "error", "error": "predicted and actual values required"}

        predicted = float(predicted)
        actual = float(actual)

        if formula_id not in self._state["formulas"]:
            self._state["formulas"][formula_id] = {
                "samples": [],
                "tier": "bronze",
                "coefficients": {"bias": 0.0, "scale": 1.0},
                "executions": 0,
                "created_at": time.time(),
            }

        formula = self._state["formulas"][formula_id]
        formula["samples"].append({"predicted": predicted, "actual": actual, "ts": time.time()})
        formula["executions"] += 1
        self._save_state()

        # Auto-tune if enough samples
        min_samples = int(self.config.get("min_samples_for_training", 5))
        tuned = False
        if len(formula["samples"]) >= min_samples:
            coefficients, mae = self._fit_linear(formula["samples"])
            formula["coefficients"] = coefficients
            formula["last_mae"] = mae
            tuned = True

        tier, promoted = self._compute_tier(formula)
        if promoted:
            formula["tier"] = tier

        self._save_state()

        return {
            "status": "success",
            "formula_id": formula_id,
            "tier_level": formula["tier"],
            "updated_coefficients": formula["coefficients"],
            "promotion_flag": promoted,
            "sample_count": len(formula["samples"]),
            "auto_tuned": tuned,
            "tier_gated_by": formula.get("tier_gated_by"),
        }

    async def _tune_coefficients(self, data: Dict, params: Dict) -> Dict:
        formula_id = data.get("formula_id") or params.get("formula_id")
        execution_history = data.get("execution_history", [])

        targets = [formula_id] if formula_id else list(self._state["formulas"].keys())
        results = {}

        for fid in targets:
            if fid not in self._state["formulas"]:
                continue
            formula = self._state["formulas"][fid]
            samples = formula["samples"] + execution_history
            if len(samples) < 2:
                results[fid] = {"skipped": True, "reason": "insufficient samples"}
                continue
            coefficients, mae = self._fit_linear(samples)
            formula["coefficients"] = coefficients
            formula["last_mae"] = mae
            tier, promoted = self._compute_tier(formula)
            if promoted:
                formula["tier"] = tier
            results[fid] = {
                "coefficients": coefficients,
                "mae": mae,
                "tier_level": formula["tier"],
                "promotion_flag": promoted,
                "tier_gated_by": formula.get("tier_gated_by"),
            }

        self._save_state()
        return {
            "status": "success",
            "tune_results": results,
            "formulas_tuned": len(results),
            "tier_level": list({v.get("tier_level", "") for v in results.values() if isinstance(v, dict)}),
            "updated_coefficients": {k: v.get("coefficients", {}) for k, v in results.items()},
            "promotion_flag": any(v.get("promotion_flag") for v in results.values() if isinstance(v, dict)),
        }

    async def _evaluate_promotion(self, data: Dict, params: Dict) -> Dict:
        formula_id = data.get("formula_id") or params.get("formula_id")
        results = {}
        targets = [formula_id] if formula_id else list(self._state["formulas"].keys())

        for fid in targets:
            if fid not in self._state["formulas"]:
                continue
            formula = self._state["formulas"][fid]
            old_tier = formula["tier"]
            tier, promoted = self._compute_tier(formula)
            if promoted:
                formula["tier"] = tier
            results[fid] = {
                "old_tier": old_tier,
                "new_tier": formula["tier"],
                "promoted": promoted,
                "executions": formula["executions"],
                "sample_count": len(formula["samples"]),
                "tier_gated_by": formula.get("tier_gated_by"),
            }

        self._save_state()
        return {
            "status": "success",
            "promotion_results": results,
            "tier_level": list({v["new_tier"] for v in results.values()}),
            "updated_coefficients": {},
            "promotion_flag": any(v["promoted"] for v in results.values()),
        }

    def _get_status(self) -> Dict:
        summary = {}
        for fid, formula in self._state["formulas"].items():
            summary[fid] = {
                "tier": formula["tier"],
                "executions": formula["executions"],
                "samples": len(formula["samples"]),
                "coefficients": formula["coefficients"],
                "last_mae": formula.get("last_mae"),
            }
        tier_dist: Dict[str, int] = {}
        for f in self._state["formulas"].values():
            t = f["tier"]
            tier_dist[t] = tier_dist.get(t, 0) + 1
        return {
            "status": "success",
            "total_formulas": len(self._state["formulas"]),
            "tier_distribution": tier_dist,
            "formula_summary": summary,
            "tier_level": list(tier_dist.keys()),
            "updated_coefficients": {},
            "promotion_flag": False,
        }

    def _reset_formula(self, formula_id: Optional[str]) -> Dict:
        if not formula_id:
            return {"status": "error", "error": "formula_id required for reset"}
        if formula_id in self._state["formulas"]:
            del self._state["formulas"][formula_id]
            self._save_state()
        return {
            "status": "success",
            "formula_id": formula_id,
            "tier_level": "bronze",
            "updated_coefficients": {},
            "promotion_flag": False,
        }

    def _fit_linear(self, samples: List[Dict]) -> tuple:
        """Fit y = scale*x + bias using numpy least squares."""
        try:
            import numpy as np
            X = np.array([[s["predicted"], 1.0] for s in samples])
            y = np.array([s["actual"] for s in samples])
            result, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
            scale, bias = float(result[0]), float(result[1])
            preds = X @ result
            mae = float(np.mean(np.abs(preds - y)) / (np.mean(np.abs(y)) + 1e-9))
            return {"scale": round(scale, 6), "bias": round(bias, 4)}, round(mae, 5)
        except ImportError:
            # Fallback: simple mean ratio
            pairs = [(s["predicted"], s["actual"]) for s in samples if s["predicted"] != 0]
            ratios = [a / p for p, a in pairs]
            scale = sum(ratios) / len(ratios) if ratios else 1.0
            mae = sum(abs(a - p * scale) for p, a in pairs) / len(pairs) / (sum(a for _, a in pairs) / len(pairs) + 1e-9) if pairs else 0.0
            return {"scale": round(scale, 6), "bias": 0.0}, round(mae, 5)

    def _compute_tier(self, formula: Dict) -> tuple:
        """Determine a formula's tier from both exec count AND last MAE.

        A formula must satisfy BOTH gates to be promoted:
          - n_exec  >= the tier's exec-count threshold (from _TIER_RULES)
          - last_mae < the configured promotion_mae_threshold

        A model with 200 runs but 40% error has no business reaching platinum,
        so a poor MAE caps the tier at the previous level. The returned dict
        carries `tier_gated_by` so the caller can see which gate kept a
        formula from advancing.
        """
        n_exec = formula["executions"]
        current_tier = formula["tier"]
        tier_order = ["bronze", "silver", "gold", "platinum"]

        mae_threshold = float(self.config.get("promotion_mae_threshold", 0.05))
        last_mae = formula.get("last_mae")

        # Compute the tier the exec count alone would justify.
        exec_tier = "bronze"
        for threshold, tier in _TIER_RULES:
            if n_exec >= threshold:
                exec_tier = tier

        # MAE gate: only allow advancement past bronze when MAE is known and
        # below the threshold. Unknown MAE means we have no signal yet — keep
        # the formula at bronze until tuning has produced an MAE.
        mae_passes = last_mae is not None and last_mae < mae_threshold

        if mae_passes:
            new_tier = exec_tier
            tier_gated_by = "exec_count"
        else:
            new_tier = "bronze"
            tier_gated_by = "mae_threshold"

        # Never demote: if the formula is already higher than what the gates
        # justify (e.g. MAE just regressed), keep its existing tier.
        if tier_order.index(new_tier) < tier_order.index(current_tier):
            new_tier = current_tier

        promoted = tier_order.index(new_tier) > tier_order.index(current_tier)

        # Stash the gating reason on the formula so callers can debug stalled
        # promotions without re-deriving it.
        formula["tier_gated_by"] = tier_gated_by

        return new_tier, promoted
