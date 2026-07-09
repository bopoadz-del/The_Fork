"""Cross-Domain Reasoner — Phase 4 of the Construction Management Engine.

Enables the orchestrator to reason dynamically across construction management
domains. When a user asks about one domain, the reasoner detects if other
domains are implicitly relevant, matches the query to multi-domain workflow
templates, and builds cross-domain execution plans.

Purely deterministic — no LLM calls. Runs cheaply on every turn.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.dependency_graph import DependencyGraph, Domain, TriggerCondition, _status_to_trigger
from app.core.workflow_templates import WorkflowTemplateLibrary
from app.schemas.construction_activity import ActivityType


# ── Template keyword patterns ─────────────────────────────────────────────
# Each template's trigger phrases — matched against the lowercased user message.
_TEMPLATE_TRIGGERS: Dict[str, List[str]] = {
    "new_project_setup": [
        "new project", "project setup", "start project", "initialize project",
        "set up project", "project kickoff", "begin project", "create project",
    ],
    "monthly_progress": [
        "monthly report", "progress report", "monthly review", "status report",
        "progress update", "where are we", "how is the project", "project health",
        "project status", "dashboard", "monthly cycle",
    ],
    "change_order_impact": [
        "change order", "variation order", "vo impact", "change impact",
        "variation impact", "scope change", "change request", "extra work",
    ],
    "delay_to_claim": [
        "delay claim", "extension of time", "eot claim", "prolongation",
        "time impact", "delay analysis", "schedule delay claim", "claim for delay",
    ],
    "qa_defect_closeout": [
        "defect tracking", "defect closeout", "ncr tracking", "quality defect",
        "defect register", "rectification", "close out defect", "defect report",
    ],
    "submittal_to_install": [
        "submittal log", "material submittal", "approval tracking",
        "submittal tracker", "shop drawing", "submittal schedule",
    ],
    "safety_incident_response": [
        "safety incident", "incident report", "accident report",
        "stop work", "incident response", "safety investigation", "root cause",
    ],
    "commissioning_to_handover": [
        "commissioning", "practical completion", "handover",
        "pc certificate", "o&m manual", "testing and commissioning",
        "system testing", "facility handover", "turnover",
    ],
    "bim_coordination": [
        "bim model", "clash detection", "model coordination",
        "ifc model", "quantity from model", "bim analysis",
        "3d model", "digital model",
    ],
    "sustainability_assessment": [
        "carbon footprint", "sustainability", "esg report",
        "green building", "leed", "breeam", "embodied carbon",
        "carbon calculation",
    ],
}

# ── Cross-domain intent keywords ──────────────────────────────────────────
# When these keywords appear in a user message, additional domains are relevant.
_CROSS_DOMAIN_KEYWORDS: Dict[str, List[Domain]] = {
    # Schedule-related keywords that should also check cost/contract
    "delay": [Domain.COST, Domain.CONTRACT],
    "behind schedule": [Domain.COST, Domain.CONTRACT],
    "schedule slip": [Domain.COST, Domain.CONTRACT],
    "late": [Domain.COST, Domain.CONTRACT],
    "overdue": [Domain.COST, Domain.CONTRACT],
    "milestone missed": [Domain.COST, Domain.CONTRACT],
    "critical path": [Domain.COST, Domain.RESOURCE],
    "float": [Domain.COST, Domain.RESOURCE],
    "compress": [Domain.COST, Domain.RESOURCE],
    "crash": [Domain.COST, Domain.RESOURCE],
    "fast track": [Domain.COST, Domain.RESOURCE, Domain.QUALITY],
    # Cost-related keywords that should also check schedule/risk
    "over budget": [Domain.SCHEDULE, Domain.RISK],
    "cost overrun": [Domain.SCHEDULE, Domain.RISK],
    "budget variance": [Domain.SCHEDULE, Domain.RISK],
    "change order": [Domain.SCHEDULE, Domain.RISK],
    "variation": [Domain.SCHEDULE, Domain.RISK],
    # Quality-related keywords that should also check schedule/safety
    "defect": [Domain.SCHEDULE, Domain.SAFETY],
    "inspection failed": [Domain.SCHEDULE, Domain.SAFETY],
    "ncr": [Domain.SCHEDULE, Domain.SAFETY],
    "non conformance": [Domain.SCHEDULE, Domain.SAFETY],
    "rework": [Domain.SCHEDULE, Domain.COST],
    # Safety-related keywords that should also check schedule/quality
    "incident": [Domain.SCHEDULE, Domain.QUALITY],
    "accident": [Domain.SCHEDULE, Domain.QUALITY],
    "stop work": [Domain.SCHEDULE, Domain.QUALITY],
    "safety violation": [Domain.SCHEDULE, Domain.QUALITY],
    # Procurement-related keywords that should also check schedule/quality
    "delivery delayed": [Domain.SCHEDULE, Domain.QUALITY],
    "long lead": [Domain.SCHEDULE, Domain.RISK],
    "procurement": [Domain.SCHEDULE],
    "purchase order": [Domain.SCHEDULE, Domain.QUALITY],
    "material shortage": [Domain.PROCUREMENT, Domain.SCHEDULE],
    "lead time": [Domain.PROCUREMENT, Domain.SCHEDULE],
    # Contract-related keywords that should also check schedule/cost/risk
    "claim": [Domain.SCHEDULE, Domain.COST, Domain.RISK],
    "dispute": [Domain.SCHEDULE, Domain.COST, Domain.RISK],
    "eot": [Domain.SCHEDULE, Domain.COST],
    "extension of time": [Domain.SCHEDULE, Domain.COST],
    "liquidated damages": [Domain.SCHEDULE, Domain.COST],
    "fidic": [Domain.SCHEDULE, Domain.COST, Domain.RISK],
    # Commissioning keywords
    "testing": [Domain.COMMISSIONING, Domain.HANDOVER],
    "performance test": [Domain.COMMISSIONING, Domain.HANDOVER],
    # BIM keywords
    "clash": [Domain.BIM, Domain.SCHEDULE],
    "quantity takeoff": [Domain.BIM, Domain.COST],
    "qto": [Domain.BIM, Domain.COST],
    # Risk keywords
    "risk register": [Domain.SCHEDULE, Domain.COST],
    "mitigation": [Domain.SCHEDULE, Domain.COST],
}

# ── Post-tool cross-domain triggers ───────────────────────────────────────
# After a specific tool runs, these domains may be relevant next.
_POST_TOOL_DOMAIN_TRIGGERS: Dict[str, List[Domain]] = {
    "generate_wbs": [Domain.COST, Domain.PROCUREMENT],
    "boq_processor": [Domain.SCHEDULE, Domain.COST],
    "qa_qc_inspection": [Domain.SCHEDULE, Domain.COST],
    "safety_compliance_audit": [Domain.SCHEDULE, Domain.RISK],
    "parse_primavera_schedule": [Domain.COST, Domain.CONTRACT, Domain.RISK],
    "progress_tracker": [Domain.COST, Domain.QUALITY, Domain.SAFETY],
    "procurement_list_generator": [Domain.SCHEDULE, Domain.QUALITY],
    "claims_builder": [Domain.SCHEDULE, Domain.COST],
    "commissioning_checklist": [Domain.HANDOVER],
    "bim_analysis": [Domain.SCHEDULE, Domain.COST],
    "cash_flow_forecast": [Domain.CONTRACT],
    "payment_certificate": [Domain.COST],
    "esg_sustainability_report": [Domain.COST, Domain.PROCUREMENT],
}


class TemplateMatcher:
    """Match user messages to multi-domain workflow templates."""

    def __init__(self, template_library: Optional[WorkflowTemplateLibrary] = None) -> None:
        self._library = template_library or WorkflowTemplateLibrary()

    def match(self, user_message: str) -> List[Tuple[str, float]]:
        """Return template IDs sorted by match score (highest first).

        Scoring: +1.0 per trigger phrase found in the message.
        Normalized by number of trigger phrases for the template.
        """
        text = user_message.lower()
        words = set(re.findall(r"[a-z0-9']+", text))
        scores: List[Tuple[str, float]] = []
        for template_id, triggers in _TEMPLATE_TRIGGERS.items():
            hits = sum(
                1
                for t in triggers
                if t in text or all(w in words for w in t.split())
            )
            if hits > 0:
                score = hits / len(triggers)  # normalize
                scores.append((template_id, score))
        scores.sort(key=lambda x: -x[1])
        return scores

    def best_match(self, user_message: str, threshold: float = 0.15) -> Optional[str]:
        """Return the best matching template ID, or None if below threshold."""
        matches = self.match(user_message)
        if matches and matches[0][1] >= threshold:
            return matches[0][0]
        return None

    def get_matched_template(self, user_message: str, threshold: float = 0.15):
        """Return the actual WorkflowTemplate object for the best match."""
        template_id = self.best_match(user_message, threshold)
        if template_id:
            return self._library.get(template_id)
        return None


class CrossDomainIntentDetector:
    """Detect when a query about one domain implies other domains."""

    def __init__(self, dependency_graph: Optional[DependencyGraph] = None) -> None:
        self._graph = dependency_graph or DependencyGraph()

    def detect_additional_domains(self, user_message: str) -> Set[Domain]:
        """Find domains that are implicitly relevant based on message keywords."""
        text = user_message.lower()
        additional: Set[Domain] = set()
        for keyword, domains in _CROSS_DOMAIN_KEYWORDS.items():
            if keyword in text:
                additional.update(domains)
        return additional

    def get_cross_domain_context(self, user_message: str) -> str:
        """Generate a context string for the system prompt about cross-domain relevance.

        Returns empty string when no cross-domain intent is detected.
        """
        domains = self.detect_additional_domains(user_message)
        if not domains:
            return ""

        domain_names = [d.value for d in domains]
        # Find dependency rules that connect to these domains
        relevant_rules = []
        for domain in domains:
            rules = self._graph.get_rules_for_target(domain)
            for rule in rules[:3]:  # cap to avoid bloat
                relevant_rules.append(rule.description)

        if not relevant_rules:
            return f""

        context_lines = [
            f"Cross-domain relevance detected: {', '.join(domain_names)} may be affected.",
            "Relevant construction management linkages:",
        ]
        for i, desc in enumerate(relevant_rules[:5], 1):
            context_lines.append(f"  {i}. {desc}")
        return "\n".join(context_lines)

    def suggest_follow_up_tools(
        self, last_tool_name: str, user_message: str
    ) -> List[str]:
        """Suggest tool names that may be relevant after a tool has run.

        Maps from the post-tool domain triggers to likely block/tool names.
        """
        triggered_domains = _POST_TOOL_DOMAIN_TRIGGERS.get(last_tool_name, [])
        if not triggered_domains:
            return []

        # Map domains to common tool names
        domain_tools: Dict[Domain, List[str]] = {
            Domain.SCHEDULE: ["generate_wbs", "parse_primavera_schedule", "progress_tracker"],
            Domain.COST: ["boq_processor", "cash_flow_forecast", "cost_estimate"],
            Domain.QUALITY: ["qa_qc_inspection"],
            Domain.SAFETY: ["safety_compliance_audit"],
            Domain.PROCUREMENT: ["procurement_list_generator"],
            Domain.CONTRACT: ["claims_builder", "contract_clause"],
            Domain.RISK: ["risk_register"],
            Domain.COMMISSIONING: ["commissioning_checklist"],
            Domain.HANDOVER: ["handover_checklist"],
            Domain.BIM: ["bim_analysis"],
            Domain.DOCUMENT: ["search_project_documents"],
            Domain.RESOURCE: ["resource_histogram"],
        }

        suggestions: List[str] = []
        for domain in triggered_domains:
            suggestions.extend(domain_tools.get(domain, []))
        return suggestions[:5]  # cap suggestions


class MultiDomainPlanBuilder:
    """Build execution plans that chain multiple domains.

    Takes a workflow template and converts it into a sequence of
    block executions the orchestrator can run.
    """

    def __init__(self, template_library: Optional[WorkflowTemplateLibrary] = None) -> None:
        self._library = template_library or WorkflowTemplateLibrary()

    def build_from_template(
        self, template_id: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Build an executable plan from a workflow template.

        Returns a dict with:
            - template_id, template_name
            - understanding (the template's plan understanding)
            - steps: list of {block, params, description}
        """
        template = self._library.get(template_id)
        if template is None:
            return None

        plan = template.build_plan(params or {})
        from app.core.cm_step_aliases import resolve_step

        steps: List[Dict[str, Any]] = []
        for step in plan.steps:
            target = resolve_step(step.type)
            if not target:
                continue
            block_name, canonical_action = target
            params_out: Dict[str, Any] = {**step.args}
            # Prefer canonical ConstructionContainer / block action so
            # /v1/execute can dispatch without a second alias hop.
            if canonical_action:
                params_out["action"] = canonical_action
            elif block_name == "construction":
                params_out["action"] = step.type
            steps.append({
                "block": block_name,
                "description": step.description,
                "params": params_out,
                "step_type": step.type,
            })

        return {
            "template_id": template_id,
            "template_name": template.name,
            "understanding": plan.understanding,
            "steps": steps,
            "domains": template.domains,
        }

    @staticmethod
    def _step_type_block_map() -> Dict[str, str]:
        """Map workflow step types to block names (compat wrapper)."""
        from app.core.cm_step_aliases import STEP_TO_TARGET

        return {step: block for step, (block, _action) in STEP_TO_TARGET.items()}


class SystemPromptInjector:
    """Inject cross-domain awareness into agent system prompts.

    This is a minimal, deterministic prompt extension — no LLM calls.
    """

    def __init__(
        self,
        template_matcher: Optional[TemplateMatcher] = None,
        intent_detector: Optional[CrossDomainIntentDetector] = None,
    ) -> None:
        self._matcher = template_matcher or TemplateMatcher()
        self._detector = intent_detector or CrossDomainIntentDetector()
        self._plan_builder = MultiDomainPlanBuilder()

    def inject_for_turn(
        self, base_prompt: str, user_message: str, conversation_history: Optional[List[Dict]] = None
    ) -> str:
        """Return an enhanced system prompt with cross-domain context.

        If no cross-domain context is relevant, returns the base prompt unchanged.
        """
        extras: List[str] = []

        # 1. Check for template match
        template = self._matcher.get_matched_template(user_message)
        if template:
            extras.append(
                f"\n[Workflow Template Match: {template.name}]\n"
                f"This request matches the '{template.name}' workflow "
                f"({', '.join(template.domains)}). "
                f"When appropriate, chain across these domains rather than "
                f"answering from a single domain."
            )

        # 2. Check for cross-domain intent
        cross_domain = self._detector.get_cross_domain_context(user_message)
        if cross_domain:
            extras.append(f"\n[Cross-Domain Context]\n{cross_domain}")

        # 3. Check conversation history for prior tool calls that suggest follow-ups
        if conversation_history:
            follow_up = self._history_based_suggestions(conversation_history)
            if follow_up:
                extras.append(f"\n[Follow-up Suggestions]\n{follow_up}")

        if not extras:
            return base_prompt

        injected = base_prompt + "\n" + "\n".join(extras)
        return injected

    def _history_based_suggestions(self, history: List[Dict]) -> str:
        """Analyze conversation history for cross-domain follow-up opportunities.

        Looks for prior assistant tool calls and suggests related domains.
        """
        if not history:
            return ""

        # Find the last assistant message with tool calls
        last_tool_domains: List[str] = []
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                content = turn.get("content", "")
                # Detect tool call mentions in history
                for tool_name, domains in _POST_TOOL_DOMAIN_TRIGGERS.items():
                    if tool_name in content:
                        last_tool_domains.extend(d.value for d in domains)
                break

        if not last_tool_domains:
            return ""

        unique = list(dict.fromkeys(last_tool_domains))  # dedupe preserve order
        return (
            "Based on the previous tool call, these domains may be relevant next: "
            f"{', '.join(unique)}. Consider chaining if the user asks a follow-up."
        )


class CrossDomainReasoner:
    """Unified facade for Phase 4 cross-domain reasoning.

    Combines template matching, intent detection, plan building, and
    prompt injection into one interface.
    """

    def __init__(self) -> None:
        self.template_matcher = TemplateMatcher()
        self.intent_detector = CrossDomainIntentDetector()
        self.plan_builder = MultiDomainPlanBuilder()
        self.prompt_injector = SystemPromptInjector(
            self.template_matcher, self.intent_detector
        )

    def analyze_turn(
        self, user_message: str, conversation_history: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """Analyze a single turn and return cross-domain reasoning results.

        Returns:
            - matched_template: best matching template (or None)
            - matched_template_score: match confidence
            - additional_domains: implicitly relevant domains
            - cross_domain_context: prompt-ready context string
            - suggested_tools: tools to consider based on cross-domain links
        """
        template_scores = self.template_matcher.match(user_message)
        best_template = template_scores[0][0] if template_scores else None
        best_score = template_scores[0][1] if template_scores else 0.0

        additional_domains = self.intent_detector.detect_additional_domains(user_message)
        cross_domain_context = self.intent_detector.get_cross_domain_context(user_message)

        # Build suggested tools from additional domains
        domain_tools = {
            Domain.SCHEDULE: ["generate_wbs", "parse_primavera_schedule"],
            Domain.COST: ["boq_processor", "cash_flow_forecast"],
            Domain.QUALITY: ["qa_qc_inspection"],
            Domain.SAFETY: ["safety_compliance_audit"],
            Domain.PROCUREMENT: ["procurement_list_generator"],
            Domain.CONTRACT: ["claims_builder"],
            Domain.RISK: ["risk_register"],
            Domain.BIM: ["bim_analysis"],
            Domain.COMMISSIONING: ["commissioning_checklist"],
            Domain.HANDOVER: ["handover_checklist"],
            Domain.DOCUMENT: ["search_project_documents"],
            Domain.RESOURCE: ["resource_histogram"],
        }
        suggested_tools: List[str] = []
        for domain in additional_domains:
            suggested_tools.extend(domain_tools.get(domain, []))
        suggested_tools = list(dict.fromkeys(suggested_tools))[:6]

        return {
            "matched_template": best_template,
            "matched_template_score": round(best_score, 3),
            "additional_domains": [d.value for d in additional_domains],
            "cross_domain_context": cross_domain_context,
            "suggested_tools": suggested_tools,
        }

    def build_plan(self, template_id: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Build a multi-domain execution plan from a template."""
        return self.plan_builder.build_from_template(template_id, params)

    def inject_prompt(
        self, base_prompt: str, user_message: str, history: Optional[List[Dict]] = None
    ) -> str:
        """Inject cross-domain awareness into a system prompt."""
        return self.prompt_injector.inject_for_turn(base_prompt, user_message, history)

    def get_post_tool_suggestions(self, last_tool_name: str, user_message: str) -> List[str]:
        """Get tool suggestions for follow-up after a tool has run."""
        return self.intent_detector.suggest_follow_up_tools(last_tool_name, user_message)
