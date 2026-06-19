"""Action → domain hint translation for the chat router.

The `smart_orchestrator` block returns action strings like `boq_process` or
`spec_analyze` based on keyword matches. The chat router uses this module to
turn those action strings into a short domain-aware system prompt that biases
the LLM toward the right kind of answer.

Why a hint, not a block dispatch:
  - Most actions returned by the orchestrator (`tender_bid_analysis`,
    `claims_builder`, `safety_compliance_audit`, ...) don't have a 1:1 block
    in BLOCK_REGISTRY — only ~9 of 39 do.
  - The blocks that exist (`boq_processor`, `spec_analyzer`, `bim`, ...)
    expect structured input (file paths, parameters), not a freeform chat
    message. Dispatching a raw user message to them would fail in ways the
    user can't recover from.
  - The chat block (DeepSeek) is already the right thing to answer freeform
    questions about uploaded files — it just needs a nudge about *which*
    domain the user is asking inside of.

So this module returns a one-sentence system-prompt fragment per action.
Unknown / weak matches return None, and the chat router falls through to
plain chat unchanged.
"""

from typing import Optional


# Per-action system-prompt hint. Each value tells the LLM what domain frame to
# answer inside. Kept short so it never overwhelms the user's question.
ACTION_HINTS: dict[str, str] = {
    # BOQ / Cost
    "boq_process":
        "The user is asking about a Bill of Quantities. Focus on line items, "
        "quantities, unit rates, totals, and any cost structure visible in the file.",
    "extract_quantities":
        "The user wants quantity takeoff data — areas, counts, volumes. "
        "Extract concrete numbers with units; flag any missing measurements.",
    "estimate_costs":
        "The user wants a cost estimate. Use any uploaded BOQ / pricing data "
        "to give specific figures (with currency) and a clear total.",
    "tender_bid_analysis":
        "The user is analysing tender/bid documents. Focus on price comparisons, "
        "scope coverage, conditions, and discrepancies between bids.",
    "procurement_list_generator":
        "The user wants a procurement / material list. Group by trade or "
        "material category, give quantities + unit, leave price columns blank "
        "if no rate data is present.",
    "procurement_optimizer":
        "The user wants procurement optimisation. Compare suppliers, identify "
        "cheapest viable options, flag any quality/lead-time tradeoffs.",
    "payment_certificate":
        "The user is dealing with a payment certificate / valuation. Focus on "
        "work done to date, retention, materials on site, and net amount due.",
    "cash_flow_forecast":
        "The user wants a cash flow / S-curve projection. Walk through phasing, "
        "monthly drawdowns, and the timing of payments.",
    # Specifications
    "spec_analyze":
        "The user is asking about a technical specification. Identify "
        "applicable standards (ASTM, ACI, SASO, etc.), grade requirements, "
        "and compliance checks.",
    "process_specification_full":
        "The user wants a structured walk-through of a full specification "
        "section. Organise by clause/sub-clause and call out compliance items.",
    # Drawings
    "drawing_qto":
        "The user is taking off quantities from drawings (DXF/DWG/floor plan). "
        "Focus on areas, lengths, counts, and call out measurement assumptions.",
    # Schedule
    "parse_primavera_schedule":
        "The user is reviewing a Primavera P6 / .xer schedule. Focus on "
        "activities, durations, critical path, and milestones.",
    "progress_tracker":
        "The user wants a progress / actual-vs-planned analysis. Identify "
        "completion %, slippages, and recovery options.",
    "resource_histogram":
        "The user wants a resource histogram (manpower / crew loading). "
        "Time-phase by week or month and flag peaks/troughs.",
    "forensic_delay_analysis":
        "The user is building a forensic delay / EOT analysis. Focus on "
        "as-planned vs as-built, concurrent delays, and entitlement basis.",
    # BIM / IFC
    "bim_analysis":
        "The user is reviewing a BIM / IFC model. Focus on building elements, "
        "quantities by type, and any model-data quality issues.",
    "bim_clash_detection":
        "The user is asking about BIM clashes. Focus on hard/soft clashes by "
        "discipline (MEP/structure/architecture) and priority.",
    "bim_extractor":
        "The user wants quantities extracted from a BIM model. Group by "
        "element type with totals; include units.",
    "digital_twin_sync":
        "The user is asking about digital-twin / as-built model sync. Focus on "
        "deltas between design and built state.",
    # QA/QC
    "qa_qc_inspection":
        "The user is dealing with QA/QC, inspections, NCRs, or punch lists. "
        "Focus on defects, root cause if known, and required corrective actions.",
    "commissioning_checklist":
        "The user wants a commissioning / handover checklist. Organise by "
        "system (HVAC, electrical, plumbing, ...) with pre/post tests.",
    # Contracts / Claims
    "process_contract":
        "The user is reviewing a contract. Focus on parties, scope, "
        "key clauses (FIDIC / NEC where applicable), and risk allocation.",
    "change_order_impact":
        "The user wants change-order / variation impact analysis. Walk through "
        "cost impact, time impact, and contract basis.",
    "variation_order_manager":
        "The user is managing variation orders. Track status, value, and "
        "approval state per VO.",
    "claims_builder":
        "The user is building a construction claim. Identify the event, basis "
        "in contract, loss/expense quantum, and time entitlement.",
    "rfi_generator":
        "The user wants help drafting an RFI. State the issue clearly, the "
        "drawings/specs involved, and the question to designer.",
    # Safety
    "safety_compliance_audit":
        "The user is doing a safety / HSE compliance check. Reference OSHA "
        "or local regs where applicable; focus on hazards and PPE.",
    "risk_register_auto_populate":
        "The user is populating a risk register. Each risk: likelihood, "
        "impact, owner, mitigation, residual rating.",
    # Sustainability
    "carbon_footprint_calculator":
        "The user wants embodied / operational carbon calculations. Use "
        "EPDs where available; output in tCO2e with assumptions stated.",
    "esg_sustainability_report":
        "The user is preparing an ESG / sustainability report. Use LEED / "
        "BREEAM categories if appropriate.",
    # Reports
    "daily_site_report":
        "The user wants a daily site report. Include manpower, weather, "
        "work done, materials received, incidents, photos referenced.",
    "submittal_log_generator":
        "The user is managing a submittal log. Track item, spec section, "
        "submitted-on, approved-on, status.",
    "as_built_deviation_report":
        "The user wants an as-built deviation report. Compare to design, "
        "list deviations with rationale and approval refs.",
    "warranty_maintenance_schedule":
        "The user wants a warranty / planned maintenance schedule. Organise "
        "by asset/system with frequency.",
    "om_manual_generator":
        "The user wants an O&M manual outline. Standard sections: "
        "operation, maintenance, spares, contacts.",
    # Value / Analysis
    "value_engineering":
        "The user wants value-engineering options. List alternatives with "
        "cost/quality tradeoffs and lifecycle implications.",
    "sympy_reason":
        "The user wants symbolic / variance analysis. Show formulas, "
        "substitutions, and the final numeric result.",
    # Documents
    "process_document":
        "The user wants help analysing the uploaded document. Be specific "
        "about what's in it; don't ask for clarification if the content is clear.",
    # Reasoning / AI
    "intelligent_workflow":
        "The user wants a multi-step workflow. Lay out the plan, then "
        "execute step-by-step.",
    "health_check":
        "The user is asking about system status. Be brief and factual.",
}


# Minimum orchestrator confidence score before we apply a hint. Below this,
# the match is too speculative — fall through to plain chat.
#
# Sized for the orchestrator's word-boundary matcher: 0.4 means "at least one
# multi-word match (e.g. 'bill of quantities' → 3 * 0.2 = 0.6, well over)" OR
# "two independent single-word matches (e.g. 'aci' + 'specification' → 0.4)".
# The previous 0.6 was tuned against the old substring matcher whose scores
# were inflated by spurious substring hits ('spec' inside 'specification', etc.).
HINT_CONFIDENCE_THRESHOLD = 0.4


# ─────────────────────────────────────────────────────────────────────────────
# Intent-based routing: chat → heavy-reasoning agent
# ─────────────────────────────────────────────────────────────────────────────
#
# Some user intents need *multi-step* generative reasoning, not a single LLM
# turn over the prompt. "Create a 200-activity schedule", "Generate a
# procurement list", "Run the forensic delay analysis" — these are NOT
# answerable by prepending file content to a chat block. They need:
#   - tool calls (search_project_documents, construction.generate_wbs,
#     formula_executor_v2)
#   - iterative refinement
#   - validation
#
# The runtime `heavy-reasoning` agent already supports all of this. When the
# orchestrator classifies a message into one of the GENERATIVE_INTENTS below
# at sufficient confidence, the chat router routes the message there instead
# of the fast single-shot ChatBlock path.
#
# Fast-path intents (everything else) stay on the chat block — single LLM
# call, sub-2s latency, fine for Q&A like "what is the total cost".

# Confidence threshold for routing.
#
# Originally 0.5 (stricter than the 0.4 hint threshold) on the theory that
# routing is more invasive (5-15s vs sub-2s) and should require firmer
# evidence. In practice the orchestrator's keyword matcher gives the real-
# world phrasing "Create a 200 activities schedule" exactly 0.4 — so 0.5
# made routing unreachable for the canonical use case we built it for.
#
# At 0.4 the whitelist (GENERATIVE_INTENTS) does the safety work: a 0.4
# match against a non-generative action (e.g. "process_document") still
# stays on the fast path because the action isn't whitelisted. Only
# genuinely generative intents with at least one multi-word OR two single-
# word keyword matches reach the heavy-reasoning path.
ROUTING_CONFIDENCE_THRESHOLD = 0.4

# Gate-2 for actions in GENERATIVE_INTENTS only. Operator brief PR #80:
# the global 0.4 threshold made the literal phrasing "generate a WBS for
# a 10-floor tower" unreachable — the single matched keyword "wbs" scores
# 0.2 (1 word), gets boosted past gate-1 by the matching relaxed gate in
# smart_orchestrator._match_actions, but used to be blocked here. Lowered
# to 0.2 so generative intents route from sparse phrasings; non-generative
# matches still need 0.4 (preserved by leaving ROUTING_CONFIDENCE_THRESHOLD
# as the global constant for any caller that consults it directly).
GENERATIVE_ROUTING_THRESHOLD = 0.2

# Generative intents — the ones that require multi-step reasoning or
# synthesis. Keep this list short and only include actions whose answer
# legitimately needs tool calls, NOT every action that has a hint.
GENERATIVE_INTENTS = frozenset({
    # Multi-step / programmatic generation
    "generate_wbs",                # create a WBS / schedule activity list
    "intelligent_workflow",        # generic multi-step workflow
    "sympy_reason",                # symbolic / variance analysis pipeline
    # Schedule analysis (real CPM, not just prose answer)
    "parse_primavera_schedule",
    "forensic_delay_analysis",
    "resource_histogram",
    # Cost/financial workflows
    "cash_flow_forecast",
    "estimate_costs",
    "procurement_list_generator",
    "procurement_optimizer",
    "payment_certificate",
    # Specs / standards full pipeline
    "process_specification_full",
    # BIM full pipeline
    "bim_analysis",
    "bim_clash_detection",
    "bim_extractor",
    # Drawing quantity take-off — real tool dispatch, not RAG-only prose.
    # Without this, "extract quantities from the floor plan" stays on the
    # fast chat path and the model has no way to read the DXF/vector PDF,
    # so it refuses to answer instead of calling drawing_qto. Surfaced as
    # the lone FAIL in QA v3 (2026-06-17).
    "drawing_qto",
    # Contracts / Claims (real document synthesis)
    "claims_builder",
    "rfi_generator",
    "value_engineering",
    "change_order_impact",
    "variation_order_manager",
})


def needs_planning(action: Optional[str], confidence: float) -> bool:
    """True iff this orchestrator classification warrants the heavy-reasoning
    agent path instead of the fast single-shot chat block.

    Uses the relaxed GENERATIVE_ROUTING_THRESHOLD (0.2) rather than the
    global ROUTING_CONFIDENCE_THRESHOLD (0.4) so sparse generative
    phrasings like "generate a WBS for a 10-floor tower" route to
    heavy-reasoning. The whitelist (GENERATIVE_INTENTS) does the safety
    work: non-generative actions still return False even at high
    confidence. Ambiguous classifications fall through to the fast path
    with a domain hint.
    """
    if not action:
        return False
    if action not in GENERATIVE_INTENTS:
        return False
    return confidence >= GENERATIVE_ROUTING_THRESHOLD


def hint_for_action(action: str) -> Optional[str]:
    """Return the LLM system-prompt hint for a routed action, or None."""
    return ACTION_HINTS.get(action)


def best_action(orchestrator_result: dict) -> tuple[Optional[str], float]:
    """Pull the highest-confidence action out of a SmartOrchestratorBlock result.

    Returns (action_name, confidence). When the orchestrator returns nothing
    routable, returns (None, 0.0). SmartOrchestratorBlock emits matches as
    {"action": str, "confidence": float, "keywords_matched": [str]} ordered
    by descending confidence.
    """
    matched = orchestrator_result.get("matched_actions") or []
    if not matched:
        return None, 0.0
    top = matched[0]
    return top.get("action"), float(top.get("confidence") or 0.0)


def hint_for_orchestrator_result(orchestrator_result: dict) -> Optional[str]:
    """End-to-end: orchestrator result → hint sentence (or None).

    Returns None when the top match is below the confidence threshold or when
    the matched action has no registered hint.
    """
    action, score = best_action(orchestrator_result)
    if not action or score < HINT_CONFIDENCE_THRESHOLD:
        return None
    return hint_for_action(action)
