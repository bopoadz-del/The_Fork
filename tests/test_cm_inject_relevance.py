"""The cross-domain prompt inject must be relevant or absent.

`_cm_prompt_fragment_for_turn` puts this text into the system prompt of
`project-assistant` and `heavy-reasoning` — the two construction agents — as
assertive domain guidance. It is not a suggestion the model can weigh against
evidence; it reads as established fact about the project.

That makes a wrong linkage worse than no linkage, which is the whole point of
these tests. Before the relevance fix, `get_cross_domain_context` took
`get_rules_for_target(domain)[:3]` — the first three rules DECLARED for each
implicated domain. Declaration order is an artefact of the order somebody typed
DEFAULT_RULES, so a variation-order question was told, in its system prompt,
that "Critical safety incident triggers stop-work order in affected zone" was a
relevant construction management linkage.
"""
from __future__ import annotations

import pytest

from app.core.cross_domain_reasoner import (
    TEMPLATE_MATCH_FLOOR,
    CrossDomainIntentDetector,
    CrossDomainReasoner,
)
from app.core.dependency_graph import Domain


@pytest.fixture
def detector():
    return CrossDomainIntentDetector()


# ── the contamination this fix exists to stop ────────────────────────────

def test_a_variation_question_is_not_told_about_safety_stop_work(detector):
    """The exact regression. R003 (safety incident -> stop work) targets
    SCHEDULE, and SCHEDULE is implicated by a variation, so declaration-order
    selection surfaced it first. Nothing about a facade variation implies a
    stop-work order, and saying so in the system prompt invites the model to
    answer as though one were in play."""
    ctx = detector.get_cross_domain_context(
        "Client issued a variation for the podium facade - what is the impact?"
    )
    assert ctx, "a variation should still produce cross-domain context"
    assert "stop-work" not in ctx.lower()
    assert "safety incident" not in ctx.lower()


def test_a_variation_question_leads_with_the_variation_rule(detector):
    """R005 is literally 'Variation order updates budget'. It should not merely
    be present, it should be first — the inject is capped at five lines and at
    800 chars downstream, so ordering decides what survives."""
    ctx = detector.get_cross_domain_context(
        "Client issued a variation for the podium facade - what is the impact?"
    )
    lines = [ln.strip() for ln in ctx.splitlines() if ln.strip().startswith("1.")]
    assert lines, ctx
    assert "variation order" in lines[0].lower()


def test_every_selected_rule_connects_to_the_message(detector):
    """The general invariant behind the two cases above: a rule earns its place
    by firing from a domain the message is about or implicates. A rule that
    merely shares a target domain is not a linkage, it is a coincidence."""
    message = "Client issued a variation for the podium facade - what is the impact?"
    sources, targets = detector.detect_domains(message)
    detected = sources | targets
    for rule in detector._rank_rules(sources, targets):
        assert rule.source_domain in detected, (
            f"{rule.rule_id} was selected but fires from "
            f"{rule.source_domain.value}, which this message never raised"
        )


def test_silence_when_no_rule_is_genuinely_relevant(detector):
    """Cross-domain intent can be detected while no dependency rule actually
    applies. The old code returned a bare `f""` on that branch — a dead
    expression that made the intent look deliberate. Now it is deliberate:
    emitting nothing is correct, because the alternative is filler presented as
    project fact."""
    assert detector.get_cross_domain_context("hello how are you") == ""


def test_no_context_is_ever_emitted_without_linkages(detector):
    """Whatever the message, the header must never appear on its own — a bare
    'Relevant construction management linkages:' with nothing under it would
    read as 'we checked and found the linkages', not 'we found none'."""
    probes = [
        "hello",
        "what is the weather",
        "we are delayed",
        "lost time injury on level 3",
        "snagging list from the handover walk",
        "clash detected between duct and beam",
    ]
    for p in probes:
        ctx = detector.get_cross_domain_context(p)
        if "linkages" in ctx:
            body = ctx.split("linkages:", 1)[1].strip()
            assert body, f"header with no linkages for: {p!r}"


# ── the gate lives in the funnel ─────────────────────────────────────────

def test_analyze_turn_withholds_a_template_below_the_floor():
    """`matched_template` used to be whatever ranked first, with no threshold,
    while the documented floor lived in `best_match`'s default argument. The
    one consumer re-checked the score itself; a second consumer that trusted
    the field would have inherited the bug."""
    reasoner = CrossDomainReasoner()
    # "dashboard" is a monthly_progress trigger but ordinary business English,
    # so it scores below the floor on its own.
    result = reasoner.analyze_turn("dashboard")

    assert 0 < result["matched_template_score"] < TEMPLATE_MATCH_FLOOR, result
    assert result["matched_template"] is None, result
    # The score is still reported, so callers can log or tune against it.
    assert result["matched_template_score"] > 0


def test_analyze_turn_reports_a_template_above_the_floor():
    reasoner = CrossDomainReasoner()
    result = reasoner.analyze_turn("we need a delay claim and extension of time")
    assert result["matched_template"] == "delay_to_claim"
    assert result["matched_template_score"] >= TEMPLATE_MATCH_FLOOR


def test_the_score_never_reports_a_template_it_withheld():
    """Whatever the message, the two fields must agree: a named template
    implies a score at or above the floor."""
    reasoner = CrossDomainReasoner()
    for msg in ["extra work", "snagging list", "lost time injury",
                "hello", "variation order impact", "float on the critical path"]:
        r = reasoner.analyze_turn(msg)
        if r["matched_template"] is not None:
            assert r["matched_template_score"] >= TEMPLATE_MATCH_FLOOR, (msg, r)


# ── scoring must not punish vocabulary ───────────────────────────────────

def test_adding_synonyms_does_not_dilute_a_match():
    """Scoring was `hits / len(triggers)`, so every synonym added to a template
    lowered the score of every phrase already in it. That put the vocabulary
    work directly at odds with the floor: enriching a template could push its
    own real matches below TEMPLATE_MATCH_FLOOR.

    Pinned as a property — one specific phrase clears the floor regardless of
    how many alternatives the template happens to list."""
    from app.core.cross_domain_reasoner import _TEMPLATE_TRIGGERS, TemplateMatcher

    matcher = TemplateMatcher()
    for template_id, triggers in _TEMPLATE_TRIGGERS.items():
        multiword = [t for t in triggers if " " in t]
        if not multiword:
            continue
        phrase = multiword[0]
        scores = dict(matcher.match(phrase))
        assert scores.get(template_id, 0.0) >= TEMPLATE_MATCH_FLOOR, (
            f"{template_id!r} does not clear the floor on its own trigger "
            f"{phrase!r} — scoring is diluted by list length"
        )


# ── site language ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "message,expected",
    [
        ("We had a lost-time injury on level 3 this morning.",
         "safety_incident_response"),
        ("The main contractor is claiming 45 days for the late foundation release.",
         "delay_to_claim"),
        ("Snagging list from the handover walk has 200 open items.",
         "qa_defect_closeout"),
        ("Client issued a variation for the podium facade.",
         "change_order_impact"),
    ],
)
def test_real_site_phrasing_reaches_the_right_template(message, expected):
    """Measured 2026-08-24: three of these matched NO template and the fourth
    matched the wrong one ("snagging ... handover" went to
    commissioning_to_handover because "handover" appeared in the sentence).

    These misses are silent — the turn simply gets no cross-domain help — so
    only a test makes them visible."""
    reasoner = CrossDomainReasoner()
    result = reasoner.analyze_turn(message)
    assert result["matched_template"] == expected, result


def test_a_procurement_question_suggests_the_procurement_tool():
    """Regression guard on the source/target split. Tools were drawn from the
    IMPLICATED domains only, and once procurement keywords carried
    PROCUREMENT as their source rather than their own target, a procurement
    question stopped suggesting the procurement tool."""
    reasoner = CrossDomainReasoner()
    result = reasoner.analyze_turn("we have a material shortage on rebar")
    assert "procurement_list_generator" in result["suggested_tools"], result


def test_source_domains_are_declared_for_every_keyword():
    """The derived back-compat view cannot drift from the routes table, but a
    keyword with a nonsense source can still be typed. Pin the shape."""
    from app.core.cross_domain_reasoner import _CROSS_DOMAIN_KEYWORDS, _KEYWORD_ROUTES

    assert set(_KEYWORD_ROUTES) == set(_CROSS_DOMAIN_KEYWORDS)
    for kw, (source, targets) in _KEYWORD_ROUTES.items():
        assert isinstance(source, Domain), kw
        assert targets, f"{kw} implicates nothing"
        assert all(isinstance(d, Domain) for d in targets), kw
        assert source not in targets, (
            f"{kw} lists its own source domain as implicated — that was the "
            f"workaround for having no source field"
        )
