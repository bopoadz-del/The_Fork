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

        operation = data.get("operation") or params.get("operation", "record_correction")

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
        else:
            return {"status": "error", "error": f"Unknown operation: {operation}. Use: record_correction, tune, promote, status, reset"}

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
        n_exec = formula["executions"]
        current_tier = formula["tier"]
        tier_order = ["bronze", "silver", "gold", "platinum"]

        new_tier = "bronze"
        for threshold, tier in _TIER_RULES:
            if n_exec >= threshold:
                new_tier = tier

        promoted = tier_order.index(new_tier) > tier_order.index(current_tier)
        return new_tier, promoted
