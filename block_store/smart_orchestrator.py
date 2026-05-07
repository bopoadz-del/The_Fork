"""Smart Orchestrator Block - 39-action keyword router for construction workflows"""

import re
from typing import Any, Dict, List, Optional, Tuple
from app.core.universal_base import UniversalBlock


# ── Action → keyword patterns ─────────────────────────────────────────────────
ACTION_PATTERNS: List[Tuple[str, List[str]]] = [
    # BOQ / Cost
    ("boq_process",           ["boq", "bill of quantities", "bill of quantity", "quantities sheet", "cost sheet", "price list", ".xlsx", ".csv"]),
    ("extract_quantities",    ["extract quantities", "take off", "qto", "quantity take", "measure", "count items"]),
    ("estimate_costs",        ["estimate cost", "cost estimate", "budget", "pricing", "price estimate", "how much"]),
    ("tender_bid_analysis",   ["tender", "bid", "proposal", "quote comparison", "contractor bid"]),
    ("procurement_list_generator", ["procurement", "material list", "purchase list", "buy list", "vendor list"]),
    ("procurement_optimizer", ["optimize procurement", "best supplier", "cheapest", "optimize cost"]),
    ("payment_certificate",   ["payment cert", "valuation", "progress payment", "invoice", "certificate"]),
    ("cash_flow_forecast",    ["cash flow", "s-curve", "payment schedule", "fund flow"]),
    # Specifications
    ("spec_analyze",          ["spec", "specification", "material spec", "grade requirement", "astm", "aci", "saso", "standard", "compliance check"]),
    ("process_specification_full", ["full specification", "spec section", "csi division", "masterformat"]),
    # Drawings
    ("drawing_qto",           ["drawing", "dxf", "dwg", "floor plan", "blueprint", "autocad", "measure drawing"]),
    ("extract_quantities",    ["area calculation", "room area", "floor area", "calculate area"]),
    # Schedule
    ("parse_primavera_schedule", ["primavera", "xer", "p6", "schedule", "gantt", "programme", "baseline"]),
    ("progress_tracker",      ["progress", "completion", "percent complete", "actual vs planned", "delay"]),
    ("resource_histogram",    ["resource", "manpower", "histogram", "crew", "labor loading", "workforce"]),
    ("forensic_delay_analysis", ["delay analysis", "eot", "extension of time", "delay claim", "forensic"]),
    # BIM / IFC
    ("bim_analysis",          ["bim", "ifc", "revit", "3d model", "building model", "navisworks"]),
    ("bim_clash_detection",   ["clash", "clash detection", "interference", "conflict", "mep conflict"]),
    ("bim_extractor",         ["extract bim", "ifc quantities", "bim quantities", "model quantities"]),
    ("digital_twin_sync",     ["digital twin", "asset data", "sync model", "as-built bim"]),
    # QA/QC
    ("qa_qc_inspection",      ["qa", "qc", "quality", "inspection", "test report", "ncr", "non-conformance", "punch list"]),
    ("commissioning_checklist", ["commissioning", "handover", "pre-commissioning", "startup checklist"]),
    # Contracts / Claims
    ("process_contract",      ["contract", "subcontract", "agreement", "terms", "clause", "fidic", "nec"]),
    ("change_order_impact",   ["change order", "co", "variation", "scope change", "amendment"]),
    ("variation_order_manager", ["variation order", "vo", "variation management", "change log"]),
    ("claims_builder",        ["claim", "dispute", "loss and expense", "damages", "extension of time"]),
    ("rfi_generator",         ["rfi", "request for information", "query", "clarification", "design query"]),
    # Safety
    ("safety_compliance_audit", ["safety", "hse", "osha", "risk assessment", "hazard", "ppe", "toolbox"]),
    ("risk_register_auto_populate", ["risk register", "risk log", "risk matrix", "risk assessment"]),
    # Sustainability
    ("carbon_footprint_calculator", ["carbon", "co2", "emissions", "sustainability", "embodied carbon", "lca"]),
    ("esg_sustainability_report", ["esg", "green", "leed", "breeam", "environmental report", "sustainability report"]),
    # Reports
    ("daily_site_report",     ["daily report", "site diary", "dsr", "site report", "daily log"]),
    ("submittal_log_generator", ["submittal", "transmittal", "document log", "material approval", "shop drawing"]),
    ("as_built_deviation_report", ["as-built", "as built", "deviation", "red-line", "record drawing"]),
    ("warranty_maintenance_schedule", ["warranty", "maintenance", "service schedule", "pppm", "o&m"]),
    ("om_manual_generator",   ["o&m", "operation manual", "maintenance manual", "handover manual"]),
    # Value / Analysis
    ("value_engineering",     ["value engineering", "ve study", "cost reduction", "alternative", "optimization"]),
    ("sympy_reason",          ["variance analysis", "reasoning", "compare cost", "benchmark", "symbolic", "formula"]),
    # Documents
    ("process_document",      ["document", "pdf", "report", "upload", "analyse file", "analyze file"]),
    # Reasoning / AI
    ("intelligent_workflow",  ["workflow", "automate", "chain", "pipeline", "multi-step", "full analysis"]),
    ("health_check",          ["health", "status", "ping", "alive", "system check"]),
]

# File extension → action
FILE_TYPE_MAP: Dict[str, str] = {
    ".xlsx": "boq_process",
    ".xls":  "boq_process",
    ".csv":  "boq_process",
    ".xer":  "parse_primavera_schedule",
    ".ifc":  "bim_analysis",
    ".dxf":  "drawing_qto",
    ".dwg":  "drawing_qto",
    ".pdf":  "process_document",
    ".jpg":  "qa_qc_inspection",
    ".jpeg": "qa_qc_inspection",
    ".png":  "qa_qc_inspection",
}

# Actions that can run in parallel safely
PARALLEL_GROUPS: Dict[str, List[str]] = {
    "full_analysis": [
        "boq_process", "spec_analyze", "drawing_qto", "parse_primavera_schedule"
    ],
    "cost_suite": [
        "extract_quantities", "estimate_costs", "procurement_list_generator"
    ],
    "compliance_suite": [
        "spec_analyze", "qa_qc_inspection", "safety_compliance_audit"
    ],
    "reporting_suite": [
        "daily_site_report", "progress_tracker", "cash_flow_forecast"
    ],
}


class SmartOrchestratorBlock(UniversalBlock):
    name = "smart_orchestrator"
    version = "1.0.0"
    description = "39-action construction keyword router: maps user messages to action queues with parallel execution hints"
    layer = 2
    tags = ["infrastructure", "construction", "orchestration", "routing", "nlp"]
    requires = []

    default_config = {
        "max_actions": 5,
        "confidence_threshold": 0.3,
        "fallback_agent": "intelligent_workflow",
    }

    ui_schema = {
        "input": {
            "type": "text",
            "placeholder": "Describe what you need (e.g. 'analyze the BOQ and check specs')...",
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "action_queue", "type": "list", "label": "Action Queue"},
                {"name": "parallel_flag", "type": "boolean", "label": "Run Parallel"},
                {"name": "fallback_agent", "type": "text", "label": "Fallback"},
            ],
        },
        "quick_actions": [
            {"icon": "🔀", "label": "Route Message", "prompt": "What actions should I run for this request?"},
            {"icon": "📋", "label": "List Actions", "prompt": "List all available construction actions"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        user_message = (
            data.get("user_message")
            or data.get("message")
            or (input_data if isinstance(input_data, str) else "")
        )
        session_context = data.get("session_context", {})
        file_type = (
            data.get("file_type")
            or params.get("file_type")
            or self._detect_file_type(data, session_context)
        )

        if user_message.strip().lower() in ("list actions", "help", "what can you do"):
            return self._list_actions()

        matched = self._match_actions(user_message, file_type)
        max_actions = int(params.get("max_actions", self.config.get("max_actions", 5)))
        action_queue = [m["action"] for m in matched[:max_actions]]

        parallel_group = self._detect_parallel_group(action_queue)
        parallel_flag = parallel_group is not None or len(action_queue) > 1

        fallback = self.config.get("fallback_agent", "intelligent_workflow")
        if not action_queue:
            action_queue = [fallback]
            parallel_flag = False

        return {
            "status": "success",
            "action_queue": action_queue,
            "parallel_flag": parallel_flag,
            "parallel_group": parallel_group,
            "fallback_agent": fallback,
            "matched_actions": matched[:max_actions],
            "file_type_hint": file_type,
            "session_context": session_context,
        }

    def _match_actions(self, message: str, file_type: Optional[str]) -> List[Dict]:
        lower = message.lower()
        scores: Dict[str, float] = {}

        # File type gives strong signal
        if file_type and file_type in FILE_TYPE_MAP:
            action = FILE_TYPE_MAP[file_type]
            scores[action] = scores.get(action, 0.0) + 0.8

        # Keyword matching
        seen_actions = set()
        for action, keywords in ACTION_PATTERNS:
            if action in seen_actions:
                continue
            for kw in keywords:
                if kw in lower:
                    weight = len(kw.split()) * 0.2  # multi-word keywords score higher
                    scores[action] = scores.get(action, 0.0) + weight
            seen_actions.add(action)

        threshold = float(self.config.get("confidence_threshold", 0.3))
        results = [
            {
                "action": action,
                "confidence": round(min(score, 1.0), 3),
                "keywords_matched": [
                    kw for kw in next(
                        (kws for a, kws in ACTION_PATTERNS if a == action), []
                    )
                    if kw in lower
                ],
            }
            for action, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if score >= threshold
        ]
        return results

    def _detect_file_type(self, data: Dict, context: Dict) -> Optional[str]:
        for key in ("file_path", "filename", "file"):
            val = data.get(key) or context.get(key, "")
            if val:
                ext = "." + str(val).rsplit(".", 1)[-1].lower() if "." in val else ""
                if ext in FILE_TYPE_MAP:
                    return ext
        return None

    def _detect_parallel_group(self, actions: List[str]) -> Optional[str]:
        action_set = set(actions)
        for group_name, group_actions in PARALLEL_GROUPS.items():
            if len(action_set & set(group_actions)) >= 2:
                return group_name
        return None

    def _list_actions(self) -> Dict:
        unique = list(dict.fromkeys(a for a, _ in ACTION_PATTERNS))
        return {
            "status": "success",
            "action_queue": [],
            "parallel_flag": False,
            "fallback_agent": "intelligent_workflow",
            "all_actions": unique,
            "total_actions": len(unique),
            "parallel_groups": PARALLEL_GROUPS,
            "file_type_routing": FILE_TYPE_MAP,
        }
