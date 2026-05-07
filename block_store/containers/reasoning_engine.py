"""
Reasoning Engine Container
Full reasoning pipeline: SymPy + 5-stage validation + 5-tier credibility + predictive layer + evidence vault

Actions:
  reason          → full pipeline (validate → score → sympy → predict → vault)
  validate        → 5-stage validation only
  score_credibility → 5-tier credibility score
  sympy_reason    → symbolic variance analysis
  predict         → predictive engine (cost_forecast / trend_fit / evm / monte_carlo)
  vault_store     → store evidence
  vault_search    → search evidence vault
  vault_audit     → audit trail
  vault_verify    → integrity check
  chain           → build evidence chain
  health_check    → container health
"""

import time
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalContainer


class ReasoningEngineContainer(UniversalContainer):
    name = "reasoning_engine"
    version = "1.0.0"
    description = (
        "Full reasoning pipeline: SymPy symbolic math + 5-stage validation + "
        "5-tier credibility scoring + predictive layer + immutable evidence vault"
    )
    layer = 3
    tags = ["container", "reasoning", "validation", "credibility", "prediction", "evidence", "construction"]
    requires = [
        "sympy_reasoning",
        "validator",
        "credibility_scorer",
        "predictive_engine",
        "evidence_vault",
    ]

    default_config = {
        "auto_vault": True,          # auto-store results in evidence vault
        "auto_score": True,          # auto-score credibility after validation
        "pipeline_fail_fast": False,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": (
                '{"action": "reason", "boq_data": [...], "drawing_data": {}, '
                '"spec_data": {}, "historical_benchmarks": {}}'
            ),
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "validation_result",  "type": "json",    "label": "Validation"},
                {"name": "credibility",         "type": "json",    "label": "Credibility"},
                {"name": "reasoning",           "type": "json",    "label": "Reasoning"},
                {"name": "prediction",          "type": "json",    "label": "Prediction"},
                {"name": "evidence_id",         "type": "text",    "label": "Evidence ID"},
                {"name": "overall_confidence",  "type": "number",  "label": "Confidence"},
            ],
        },
        "quick_actions": [
            {"icon": "🧠", "label": "Full Reasoning",   "prompt": "Run full reasoning pipeline on this dataset"},
            {"icon": "✅", "label": "Validate",          "prompt": "Run 5-stage validation"},
            {"icon": "🏅", "label": "Score Credibility", "prompt": "Score credibility tier"},
            {"icon": "🔮", "label": "Predict",           "prompt": "Run cost forecast and trend projection"},
            {"icon": "🔒", "label": "Store Evidence",    "prompt": "Store reasoning results as evidence"},
        ],
    }

    # ── Route ──────────────────────────────────────────────────────────────────

    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        action = data.get("action") or p.get("action") or action

        handlers = {
            # Full pipeline
            "reason":             self._full_pipeline,
            # Individual stages
            "validate":           self._validate,
            "score_credibility":  self._score_credibility,
            "sympy_reason":       self._sympy_reason,
            "predict":            self._predict,
            # Evidence vault
            "vault_store":        self._vault_store,
            "vault_retrieve":     self._vault_retrieve,
            "vault_search":       self._vault_search,
            "vault_audit":        self._vault_audit,
            "vault_verify":       self._vault_verify,
            "chain":              self._chain,
            "get_chain":          self._get_chain,
            # Meta
            "health_check":       self._health_check,
            "list_actions":       self._list_actions,
        }

        handler = handlers.get(action)
        if not handler:
            return {
                "status": "error",
                "error": f"Unknown reasoning action: '{action}'",
                "available_actions": list(handlers.keys()),
            }
        return await handler(data, p)

    # ── Full Pipeline ──────────────────────────────────────────────────────────

    async def _full_pipeline(self, data: Dict, params: Dict) -> Dict:
        """
        Stage A → Validate (5 stages)
        Stage B → Score Credibility (5 tiers)
        Stage C → SymPy Reasoning (variance + recommendations)
        Stage D → Predictive Layer (cost forecast)
        Stage E → Evidence Vault (store results)
        """
        start = time.time()
        pipeline_log: List[str] = []
        fail_fast = params.get("fail_fast", self.config.get("pipeline_fail_fast", False))

        # A: Validation
        validation = await self._validate(data, params)
        pipeline_log.append(f"A-Validation: stages_passed={validation.get('stages_passed',0)}/{validation.get('total_stages',5)}, issues={validation.get('issue_count',0)}")
        if fail_fast and not validation.get("overall_pass"):
            return self._pipeline_result(data, validation, {}, {}, {}, None, pipeline_log, start)

        # B: Credibility Scoring
        credibility_input = self._build_credibility_input(data, validation)
        credibility = await self._score_credibility(credibility_input, params)
        pipeline_log.append(f"B-Credibility: tier={credibility.get('tier',1)} ({credibility.get('tier_label','')}) score={credibility.get('score',0)}")

        # C: SymPy Reasoning
        reasoning = await self._sympy_reason(data, params)
        pipeline_log.append(f"C-SymPy: {reasoning.get('items_analyzed',0)} items analyzed, {reasoning.get('high_variance_count',0)} high-variance, {len(reasoning.get('recommendations',[]))} recs")

        # D: Predictive Layer
        prediction = {}
        total_cost = self._compute_total_cost(data.get("boq_data", []))
        if total_cost > 0:
            pred_input = {
                "operation": "cost_forecast",
                "base_cost": total_cost,
                "years": data.get("forecast_years", 1),
                "escalation_rate": data.get("escalation_rate", 0.04),
            }
            prediction = await self._predict(pred_input, params)
            pipeline_log.append(f"D-Predict: base={total_cost:,.0f}, forecast={prediction.get('prediction',0):,.0f}")

        # E: Evidence Vault
        evidence_id = None
        if self.config.get("auto_vault", True):
            vault_payload = {
                "operation": "store",
                "evidence": {
                    "type": "validation_result",
                    "content": {
                        "validation": validation,
                        "credibility": credibility,
                        "reasoning_summary": {
                            "items_analyzed": reasoning.get("items_analyzed"),
                            "high_variance_count": reasoning.get("high_variance_count"),
                            "recommendation_count": len(reasoning.get("recommendations",[])),
                        },
                        "prediction_summary": {
                            "base_cost": total_cost,
                            "forecast": prediction.get("prediction"),
                        },
                    },
                    "source": "reasoning_engine_pipeline",
                    "project_id": data.get("project_id", "default"),
                    "credibility_tier": credibility.get("tier", 1),
                    "validation_stages_passed": list(range(1, validation.get("stages_passed", 0) + 1)),
                    "tags": ["pipeline", "auto"],
                },
            }
            vault_result = await self._vault_store(vault_payload, params)
            evidence_id = vault_result.get("evidence_id")
            pipeline_log.append(f"E-Vault: stored as {evidence_id}")

        return self._pipeline_result(data, validation, credibility, reasoning, prediction, evidence_id, pipeline_log, start)

    def _pipeline_result(
        self, data, validation, credibility, reasoning, prediction,
        evidence_id, pipeline_log, start
    ) -> Dict:
        # Overall confidence = weighted average
        v_score  = validation.get("credibility_score", 0)
        c_score  = credibility.get("score", 0) if credibility else 0
        r_conf   = reasoning.get("confidence", 0.8) * 100 if reasoning else 80
        overall  = round((v_score * 0.4 + c_score * 0.3 + r_conf * 0.3), 1)

        return {
            "status": "success",
            "overall_confidence": overall,
            "validation_result": validation,
            "credibility": credibility,
            "reasoning": reasoning,
            "prediction": prediction,
            "evidence_id": evidence_id,
            "pipeline_log": pipeline_log,
            "elapsed_ms": round((time.time() - start) * 1000),
        }

    # ── Individual Stage Delegators ────────────────────────────────────────────

    async def _validate(self, data: Dict, params: Dict) -> Dict:
        return await self._call("validator", data, params)

    async def _score_credibility(self, data: Dict, params: Dict) -> Dict:
        return await self._call("credibility_scorer", data, params)

    async def _sympy_reason(self, data: Dict, params: Dict) -> Dict:
        return await self._call("sympy_reasoning", data, params)

    async def _predict(self, data: Dict, params: Dict) -> Dict:
        return await self._call("predictive_engine", data, params)

    # ── Vault delegators ───────────────────────────────────────────────────────

    async def _vault_store(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "store"}, params)

    async def _vault_retrieve(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "retrieve"}, params)

    async def _vault_search(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "search"}, params)

    async def _vault_audit(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "audit_trail"}, params)

    async def _vault_verify(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "verify"}, params)

    async def _chain(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "chain"}, params)

    async def _get_chain(self, data: Dict, params: Dict) -> Dict:
        return await self._call("evidence_vault", {**data, "operation": "get_chain"}, params)

    # ── Health ─────────────────────────────────────────────────────────────────

    async def _health_check(self, data: Dict, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        sub = {b: ("registered" if b in BLOCK_REGISTRY else "missing") for b in self.requires}
        return {
            "status": "success",
            "container": self.name,
            "version": self.version,
            "sub_blocks": sub,
            "all_blocks_registered": all(v == "registered" for v in sub.values()),
        }

    async def _list_actions(self, data: Dict, params: Dict) -> Dict:
        return {
            "status": "success",
            "actions": {
                "pipeline":    ["reason"],
                "validation":  ["validate"],
                "credibility": ["score_credibility"],
                "reasoning":   ["sympy_reason"],
                "prediction":  ["predict"],
                "evidence":    ["vault_store", "vault_retrieve", "vault_search", "vault_audit", "vault_verify", "chain", "get_chain"],
                "meta":        ["health_check", "list_actions"],
            },
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _call(self, block_name: str, data: Dict, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get(block_name)
        if not cls:
            return {"status": "error", "error": f"Block '{block_name}' not registered"}
        return await cls().process(data, params)

    def _build_credibility_input(self, data: Dict, validation: Dict) -> Dict:
        stages_passed = list(range(1, validation.get("stages_passed", 0) + 1))
        items = []
        for item in data.get("boq_data", [])[:20]:
            items.append({
                "id": item.get("item_key") or item.get("description", "item"),
                "source": item.get("source", "unknown"),
                "validation_stages_passed": stages_passed,
                "cross_references": item.get("cross_references", 0),
                "age_days": item.get("age_days"),
                "expert_reviewed": item.get("expert_reviewed", False),
            })
        if not items:
            items = [{"id": "dataset", "source": "unknown", "validation_stages_passed": stages_passed}]
        return {"items": items}

    def _compute_total_cost(self, boq_data: List[Dict]) -> float:
        return sum(
            _to_float(item.get("total_cost") or (_to_float(item.get("quantity",0)) * _to_float(item.get("unit_cost") or item.get("rate",0))))
            for item in boq_data
        )


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",","").strip())
    except (ValueError, TypeError):
        return 0.0
