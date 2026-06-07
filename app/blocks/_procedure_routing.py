"""
SMART ORCHESTRATOR - CONSTRUCTION PROCEDURE ROUTING ADDITIONS
==============================================================
Procedure-specific routing patterns that prepend to ACTION_PATTERNS in
app/blocks/smart_orchestrator.py so procedure-specific queries are caught
first, before generic construction keywords.

These map real user language to the correct platform action, informed by
18 industry-standard procedures (PRC-301 through PRC-606).
"""

PROCEDURE_ROUTING_ADDITIONS = [

    # -- DESIGN MANAGEMENT (PRC-501, PRC-502) ---------------------------------
    # Design review workflows, acceptance forms, design directives
    (
        "design_review_workflow",
        [
            "design review", "review package", "review workshop",
            "TEM-501", "TEM-502", "TEM-503", "TEM-504", "TEM-505",
            "TEM-506", "TEM-507", "TEM-508", "TEM-509", "TEM-510",
            "PRC-501", "design acceptance", "design acceptance form",
            "for comment", "buy-off", "design buy off", "design comments schedule",
            "design review schedule", "review status", "design package acceptance",
            "project decision note", "PDN", "design coordination meeting",
            "design progress evaluation", "design workshop decision",
            "distribution period", "review window",
        ]
    ),
    (
        "design_directive",
        [
            "design directive", "DD-", "PRC-502", "TEM-510",
            "design instruction", "design change instruction",
            "employer instruction", "design order",
        ]
    ),

    # -- PROJECT CONTROLS (PRC-301, PRC-302, PRC-303) -------------------------
    # RFI, risk register, work package
    (
        "rfi_management",
        [
            "request for information", "PRC-301",
            "RFI-", "technical query", "technical enquiry",
            "contractor query", "design query", "clarification request",
            "rfi log", "rfi register", "open rfis", "overdue rfi",
        ]
    ),
    (
        "risk_register_auto_populate",
        [
            "PRC-302", "risk register", "risk log", "risk matrix",
            "risk score", "probability impact", "risk mitigation",
            "risk identification", "risk assessment", "risk appetite",
            "amber risk", "red risk", "green risk",
            "if then risk", "risk statement",
        ]
    ),
    (
        "work_package_control",
        [
            "PRC-303", "work package", "WP-", "package control",
            "work package register", "TEM-303",
            "package status", "package milestone", "package overdue",
        ]
    ),

    # -- QUALITY & CONSTRUCTION (PRC-401 through PRC-406) ---------------------
    # QA audit, NCR, T&C, handover, inspection, HSE
    (
        "qa_audit",
        [
            "PRC-401", "qa audit", "quality audit", "qc audit",
            "audit program", "audit programme", "audit finding",
            "critical finding", "major finding", "minor finding",
            "audit observation", "2nd party audit",
        ]
    ),
    (
        "ncr_management",
        [
            "PRC-402", "NCR", "non-conformance", "non conformance",
            "nonconformance", "NCR-", "disposition",
            "use as is", "reject and replace", "concession request",
            "corrective action", "ncr closure", "close ncr",
            "ncr register", "open ncr", "physical ncr", "documentary ncr",
        ]
    ),
    (
        "commissioning_checklist",
        [
            "PRC-403", "testing commissioning", "test and commission",
            "T&C", "ITP", "inspection test plan",
            "hold point", "witness point", "review point",
            "commissioning result", "punch list", "pre-commissioning",
            "rides scope", "mep commissioning", "building commissioning",
        ]
    ),
    (
        "handover_management",
        [
            "PRC-404", "handover", "practical completion", "CPC",
            "certificate of practical completion", "DLP",
            "defects liability", "snag list", "as-built",
            "o&m manual", "handover register", "handover checklist",
            "handover prerequisites",
        ]
    ),
    (
        "inspection_request",
        [
            "PRC-405", "inspection request", "IR-",
            "contractor inspection", "material inspection",
            "witness inspection", "hold point release",
            "inspection result", "inspection rejection",
        ]
    ),
    (
        "safety_compliance_audit",
        [
            "PRC-406", "hse audit", "hse inspection",
            "stop work", "stop work order", "near miss",
            "fatality risk", "serious injury", "environmental incident",
            "toolbox talk", "ppe compliance", "hse finding",
            "work resumption", "hse register",
        ]
    ),

    # -- TENDERING & PROCUREMENT (PRC-601 through PRC-604) --------------------
    # Job requisition, RFP, tender analysis, award
    (
        "job_requisition",
        [
            "PRC-601", "job requisition", "JR-", "JR number",
            "prequalification", "prequalify", "procurement strategy",
            "packaging strategy", "rfp preparation",
        ]
    ),
    (
        "rfp_management",
        [
            "PRC-602", "request for proposal", "RFP",
            "tender package", "instructions to tenderers",
            "form of tender", "tender documents",
            "scope of work document",
        ]
    ),
    (
        "tender_bid_analysis",
        [
            "PRC-603", "tender analysis", "tender evaluation",
            "TER", "tender evaluation report",
            "bid scoring", "bid comparison", "tender recommendation",
            "RAP", "rapid approval", "PRC-603A",
            "technical score", "commercial score",
        ]
    ),
    (
        "contract_award",
        [
            "PRC-604", "contract award", "letter of award", "LOA",
            "performance bond", "advance payment bond",
            "award approval", "award threshold",
        ]
    ),

    # -- COMMERCIAL (PRC-605, PRC-606) ----------------------------------------
    # Payments, change management, variation orders
    (
        "payment_certificate",
        [
            "PRC-605", "interim payment", "payment request", "PR-",
            "payment certificate", "payment certification",
            "retention release", "retention calculation",
            "payment workflow", "certified amount", "disputed amount",
            "cumulative billed", "payment status",
        ]
    ),
    (
        "change_order_impact",
        [
            "PRC-606", "change management", "request for modification",
            "RFM-", "variation order", "VO-",
            "provisional sum directive", "PSD",
            "type a change", "type b change", "type c change",
            "owner initiated change", "contractor claim variation",
            "regulatory change", "scope change authority",
            "variation settlement", "contract account",
        ]
    ),
]
