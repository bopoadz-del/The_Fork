"""Commercial / PM arithmetic calculators (additive library, gap-fill).

Deterministic arithmetic — currency/units are whatever the caller passes
(SAR, USD, m2, sf). Code-agnostic. Rates and quantities are parameters.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


def roi_calculator(gain: float, cost: float) -> dict:
    """Return on investment: ROI% = (gain - cost) / cost * 100."""
    g, c = float(gain), float(cost)
    if c == 0:
        return {"error": "cost must be non-zero to compute ROI"}
    net = g - c
    roi = net / c * 100.0
    return {
        "net_profit": round(net, 2),
        "roi_percent": round(roi, 2),
        "standard": "arithmetic",
        "note": f"ROI = (gain - cost)/cost*100 = ({g} - {c})/{c}*100 = {roi:.2f}%.",
    }


def unit_cost_total(quantity: float, unit_rate: float) -> dict:
    """Total = quantity * unit_rate (a line-item extension)."""
    q, r = float(quantity), float(unit_rate)
    total = q * r
    return {
        "total_cost": round(total, 2),
        "standard": "arithmetic (BOQ line item)",
        "note": f"Total = qty * rate = {q} * {r} = {total:.2f}.",
    }


def cost_per_area(total_cost: float, area: float, area_unit: str = "m2") -> dict:
    """Unit area cost = total_cost / area (per m2 or per sf, caller's unit)."""
    t, a = float(total_cost), float(area)
    if a <= 0:
        return {"error": "area must be > 0 — cannot compute a unit cost from a zero or negative area."}
    rate = t / a
    return {
        "cost_per_area": round(rate, 2),
        "area_unit": area_unit,
        "standard": "arithmetic",
        "note": f"Cost/{area_unit} = {t}/{a} = {rate:.2f} per {area_unit}.",
    }


def productivity_rate(output_quantity: float, labor_hours: float, crew_size: int = 1) -> dict:
    """Output per labour-hour and per worker-hour. rate = output/hours;
    per-worker = rate/crew_size."""
    o, h = float(output_quantity), float(labor_hours)
    if h == 0:
        return {"error": "labor_hours must be > 0"}
    rate = o / h
    per_worker = (rate / crew_size) if crew_size else rate
    return {
        "rate_per_hour": round(rate, 3),
        "rate_per_worker_hour": round(per_worker, 4),
        "crew_size": crew_size,
        "standard": "arithmetic (productivity)",
        "note": (f"Rate = output/hours = {o}/{h} = {rate:.3f}/hr; "
                 f"per worker = /{crew_size} = {per_worker:.4f}/worker-hr."),
    }


# Live OLD-pack E1: "Calculate the delay damages per calendar day in SAR
# for the whole of the Works." A5 already surfaces the rate string
# (0.1% of Contract Price per calendar day) and A2 surfaces the ACA.
# The model quoted sources and never multiplied. This path composes
# rate × ACA into a daily figure and must not invent either operand.
# Kill-switch: COMPOSE_DELAY_DAMAGES_DAILY=0 restores the FAIL (rate
# quoted, no SAR/day). Distinct from the A5 rate-rescue (#503).
_DD_ASK_RE = re.compile(r"(?i)(?:delay|liquidated)\s+damages")
_DD_CAP_KEY_RE = re.compile(
    r"(?i)\b(?:maximum|max(?:imum)?\s+amount|capped?)\b",
)
_DD_RATE_PCT_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?"
    r"(?:contract\s+price|accepted\s+contract\s+amount)"
    r"[^.]{0,48}\bper\b",
)
_DD_RATE_NEAR_LABEL_RE = re.compile(
    r"(?i)(?:delay|liquidated)\s+damages.{0,240}?"
    r"(\d+(?:\.\d+)?)\s*%[^%]{0,80}\bper\b",
)
_ACA_LABEL_RE = re.compile(
    r"(?i)\b(?:accepted\s+contract\s+amount|contract\s+price)\b",
)
_EXCL_VAT_RE = re.compile(
    r"(?i)\bexclud(?:ing|es|ed)\b.{0,12}\bvat\b|"
    r"\bexcl\.?\s*vat\b|\bexclusive\s+of\s+vat\b",
)
_INCL_VAT_RE = re.compile(
    r"(?i)\binclud(?:ing|es|ed)\b.{0,12}\bvat\b|"
    r"\bincl\.?\s*vat\b|\binclusive\s+of\s+vat\b",
)
_MONEY_RE = re.compile(
    r"(?i)\b(SAR|AED|USD|EUR|GBP|QAR|BHD|KWD|OMR)\s*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2})",
)
_POINTER_RE = re.compile(
    r"(?i)at\s+the\s+rate\s+stated\s+in\s+the\s+contract\s+data",
)
_CONTRACT_PRICE_RE = re.compile(r"(?i)\bcontract\s+price\b")
_CONTRACT_DATA_CTX_RE = re.compile(
    r"(?i)\b(?:contract\s+data|particulars|8\.8)\b",
)
_WHOLE_WORKS_RE = re.compile(r"(?i)\bwhole\s+of\s+the\s+works\b")
_SUBCLAUSE_87_RE = re.compile(r"(?i)\b(?:sub[- ]?clause\s+)?8\.7\b")
# Live leftover E1 after #535: CoC 8.7/8.8 windows state 0.015% of the
# excl-VAT ACA (SAR 263,175.67/day). Contract Data 8.8 is 0.1% of the
# Contract Price (SAR 1,754,504.46/day). First-match compose elected
# 0.015% whenever that window led the excerpts. Kill-switch
# COMPOSE_REJECT_E1_LOOKALIKE_RATE=0 restores electing 0.015%.
_LOOKALIKE_RATE_PERCENT = 0.015
_PREFERRED_WHOLE_WORKS_RATE = 0.1
# Live leftover E1 on c5c6dfa: Contract Data 8.8 chunks 9–11 carried a
# FIDIC worked-example ACA of SAR 10,000,000. Compose elected it
# (0.1% → SAR 10,000/day) instead of the filled excl-VAT row
# (~SAR 1,754,504,456.25). Kill-switch COMPOSE_REJECT_E1_TOY_ACA=0
# restores electing the first match (the FAIL).
_TOY_ACA_AMOUNT = 10_000_000.0
_TOY_DAILY_AMOUNT = 10_000.0
_CLAUSE_111_RE = re.compile(r"(?i)\b1\.1\.1\b")
_TOY_EXAMPLE_CUE_RE = re.compile(
    r"(?i)\b(?:for\s+example|worked\s+example|daily\s+amount|"
    r"if\s+the\s+accepted\s+contract\s+amount)\b",
)


def compose_delay_damages_daily_enabled() -> bool:
    """ON by default. ``COMPOSE_DELAY_DAMAGES_DAILY=0`` is the kill-switch."""
    raw = (os.getenv("COMPOSE_DELAY_DAMAGES_DAILY", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def reject_e1_toy_aca_enabled() -> bool:
    """ON by default. ``COMPOSE_REJECT_E1_TOY_ACA=0`` restores the 10M FAIL."""
    raw = (os.getenv("COMPOSE_REJECT_E1_TOY_ACA", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def reject_e1_lookalike_rate_enabled() -> bool:
    """ON by default. ``COMPOSE_REJECT_E1_LOOKALIKE_RATE=0`` keeps 0.015%."""
    raw = (
        os.getenv("COMPOSE_REJECT_E1_LOOKALIKE_RATE", "1") or "1"
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def delay_damages_rate_is_coc_lookalike(rate: float, ctx: str = "") -> bool:
    """True for CoC 8.7/8.8 0.015%-of-ACA, not Contract Data 0.1% of Price.

    Live leftover E1: chunks 9–11 restated delay damages as 0.015% of
    the filled excl-VAT ACA. That product is SAR 263,175.67/day. The
    Contract Data particular is 0.1% of the Contract Price. A genuine
    Contract Data row that itself says 0.015% of the Contract Price is
    not a lookalike.
    """
    if not reject_e1_lookalike_rate_enabled():
        return False
    if abs(float(rate) - _LOOKALIKE_RATE_PERCENT) > 1e-9:
        return False
    ctx = ctx or ""
    if _CONTRACT_PRICE_RE.search(ctx) and _CONTRACT_DATA_CTX_RE.search(ctx):
        return False
    if _SUBCLAUSE_87_RE.search(ctx):
        return True
    if _ACA_LABEL_RE.search(ctx) and not _CONTRACT_PRICE_RE.search(ctx):
        return True
    return True


def delay_damages_rate_preference_score(rate: float, ctx: str = "") -> int:
    """Higher wins for whole-of-Works E1. Contract Data 0.1% beats 0.015%."""
    ctx = ctx or ""
    if _DD_CAP_KEY_RE.search(ctx):
        return -1
    if delay_damages_rate_is_coc_lookalike(rate, ctx):
        return 0
    score = 1
    if _CONTRACT_DATA_CTX_RE.search(ctx):
        score += 4
    if _CONTRACT_PRICE_RE.search(ctx):
        score += 3
    if _WHOLE_WORKS_RE.search(ctx):
        score += 2
    if abs(float(rate) - _PREFERRED_WHOLE_WORKS_RATE) <= 1e-9:
        score += 2
    return score


def aca_amount_is_toy_example(amount: float, ctx: str = "") -> bool:
    """True for a FIDIC worked-example / placeholder ACA, not a filled row.

    Live E1: unlabeled ``SAR 10,000,000`` in an 8.8 window and its
    0.1% daily product ``SAR 10,000``. A filled 1.1.1 excluding-VAT
    particular of exactly 10M is not a toy. Non-10M/10k figures are
    never the worked example — Contract Data template cues
    (``insert``, ``for example``) must not stain the filled excl-VAT
    ACA (~SAR 1,754,504,456.25).
    """
    if not reject_e1_toy_aca_enabled():
        return False
    amt = float(amount)
    is_10m = abs(amt - _TOY_ACA_AMOUNT) <= 0.005
    is_10k = abs(amt - _TOY_DAILY_AMOUNT) <= 0.005
    if not (is_10m or is_10k):
        return False
    ctx = ctx or ""
    if is_10m and _CLAUSE_111_RE.search(ctx) and _EXCL_VAT_RE.search(ctx):
        # A neighboring filled 1.1.1 row must not un-toy the 8.8
        # worked example when excerpts are concatenated.
        if _TOY_EXAMPLE_CUE_RE.search(ctx):
            return True
        return False
    return True


def chunk_has_real_accepted_contract_amount(text: str) -> bool:
    """True when ``text`` states a non-toy ACA money figure."""
    return any(not toy for _amt, _cur, _kind, toy in _iter_aca_candidates(text))


def chunk_accepted_contract_amount_is_only_toy(text: str) -> bool:
    """True when every ACA money figure in ``text`` is a toy/example."""
    cands = _iter_aca_candidates(text)
    return bool(cands) and all(toy for _amt, _cur, _kind, toy in cands)


def query_asks_delay_damages_daily_amount(query: str) -> bool:
    """True for E1 (calculate … delay damages … in SAR), not A5 rate lookup.

    Delegates to the retriever twin so retrieval rescue and compose
    cannot drift on the ask class.
    """
    try:
        from app.core.rag.retriever import (
            query_asks_delay_damages_daily_amount as _asks,
        )
        return _asks(query)
    except Exception:  # noqa: BLE001 — never break a turn over an import
        q = query or ""
        return bool(q and _DD_ASK_RE.search(q) and re.search(
            r"(?i)\b(?:calculate|compute|work\s+out|how\s+much)\b", q,
        ) and re.search(
            r"(?i)\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b", q,
        ))


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _iter_delay_rate_candidates(text: str) -> list[tuple[float, int]]:
    """``(rate_percent, preference)`` whole-of-Works daily rates in ``text``."""
    t = text or ""
    if not t:
        return []
    out: list[tuple[float, int]] = []

    def _add(pct: float, ctx: str) -> None:
        score = delay_damages_rate_preference_score(pct, ctx)
        if score < 0:
            return
        out.append((pct, score))

    try:
        from app.core.contract_data_chunks import filled_particulars_rows
        for key, val in filled_particulars_rows(t):
            if not _DD_ASK_RE.search(key):
                continue
            if _DD_CAP_KEY_RE.search(key):
                continue
            if not re.search(r"(?i)\bper\b", val):
                continue
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", val)
            if m:
                _add(float(m.group(1)), f"{key} {val}")
    except Exception:  # noqa: BLE001 — fall through to the scanned regex
        logger.debug("particulars rate parse failed; using scanned regex", exc_info=True)
    blob = _collapse_ws(t)
    if _POINTER_RE.search(blob) and not _DD_RATE_PCT_RE.search(blob):
        return out
    for m in _DD_RATE_PCT_RE.finditer(blob):
        ctx = blob[max(0, m.start() - 96):m.end() + 48]
        _add(float(m.group(1)), ctx)
    for m in _DD_RATE_NEAR_LABEL_RE.finditer(blob):
        window = blob[max(0, m.start() - 48):m.end()]
        if _DD_CAP_KEY_RE.search(window):
            continue
        ctx = blob[max(0, m.start() - 96):m.end() + 48]
        _add(float(m.group(1)), ctx)
    return out


def parse_delay_damages_rate_percent(text: str) -> float | None:
    """Daily Delay Damages *rate* as a percentage, or None.

    A cap row (``Maximum amount of delay damages: 10%…``) and a General
    Conditions pointer are not the rate. When a CoC 8.7/8.8 window
    restates 0.015% of the ACA next to the Contract Data 0.1% of
    Contract Price, the Contract Data particular wins — first-match
    used to emit SAR 263,175.67/day. Does not invent a percentage.
    """
    cands = _iter_delay_rate_candidates(text)
    if not cands:
        return None
    preferred = [(pct, score) for pct, score in cands if score >= 2]
    # Score 0 is the CoC 0.015% lookalike. Do not compose 263,175.67
    # from that alone — retrieval must still surface Contract Data 0.1%.
    pool = preferred or [(pct, score) for pct, score in cands if score >= 1]
    if not pool:
        return None
    pool.sort(key=lambda item: -item[1])
    return pool[0][0]


def _iter_aca_candidates(text: str) -> list[tuple[float, str, str, bool]]:
    """``(amount, currency, vat_kind, is_toy)`` ACA figures in ``text``.

    ``vat_kind`` is ``excl``, ``neutral``, or ``incl``. Filled particulars
    rows first, then a 160-char scanned window. Does not invent a figure.
    """
    t = text or ""
    if not t:
        return []
    out: list[tuple[float, str, str, bool]] = []

    def _bucket(amount: float, currency: str, ctx: str) -> None:
        if amount < 1000:
            return
        # A delay-damages *rate* sentence names Contract Price / ACA as
        # the percentage base and is not itself the money row. Do not
        # stain a real Accepted Contract Amount figure just because the
        # 0.1%-per-day row sits in the same 160-char window (live E1
        # scanned Contract Data: rate chunk then excl-VAT ACA).
        if _DD_ASK_RE.search(ctx) and "%" in ctx:
            if not re.search(r"(?i)accepted\s+contract\s+amount", ctx):
                return
        if not _ACA_LABEL_RE.search(ctx):
            return
        if _EXCL_VAT_RE.search(ctx):
            kind = "excl"
        elif _INCL_VAT_RE.search(ctx):
            kind = "incl"
        else:
            kind = "neutral"
        out.append(
            (amount, currency, kind, aca_amount_is_toy_example(amount, ctx)),
        )

    try:
        from app.core.contract_data_chunks import filled_particulars_rows
        for key, val in filled_particulars_rows(t):
            money = _MONEY_RE.search(val)
            if not money:
                continue
            amount = float(money.group(2).replace(",", ""))
            _bucket(amount, money.group(1).upper(), f"{key} {val}")
    except Exception:  # noqa: BLE001 — scanned fallback still runs
        logger.debug("particulars ACA parse failed; using scanned regex", exc_info=True)

    blob = _collapse_ws(t)
    for m in _MONEY_RE.finditer(blob):
        amount = float(m.group(2).replace(",", ""))
        start = max(0, m.start() - 160)
        ctx = blob[start:m.end() + 24]
        _bucket(amount, m.group(1).upper(), ctx)

    return out


def parse_accepted_contract_amount(text: str) -> tuple[float, str] | None:
    """ACA / Contract Price money amount from client text, or None.

    Prefers an excluding-VAT figure when both incl/excl are present
    (FIDIC Contract Price / the rate's base is the net amount). When
    a FIDIC worked-example ACA (live: SAR 10,000,000) sits next to a
    filled excl-VAT row, the toy is dropped — the product must not
    invent a figure and must not elect the example. A percentage-of-
    ACA cap row is skipped.
    """
    t = text or ""
    if not t:
        return None
    buckets: dict[str, list[tuple[float, str]]] = {
        "excl": [], "neutral": [], "incl": [],
    }
    toys: dict[str, list[tuple[float, str]]] = {
        "excl": [], "neutral": [], "incl": [],
    }
    for amount, currency, kind, toy in _iter_aca_candidates(t):
        item = (amount, currency)
        (toys if toy else buckets)[kind].append(item)

    any_real = any(buckets[k] for k in ("excl", "neutral", "incl"))
    if not any_real:
        # Live leftover E1 after #529: electing the toy when no real
        # row is in-window produced SAR 10,000/day. Skip it so the
        # reservation can still surface the filled excl-VAT ACA.
        if reject_e1_toy_aca_enabled():
            return None
        source = toys
    else:
        source = buckets
    picked = source["excl"] or source["neutral"] or source["incl"]
    if not picked:
        return None
    seen: set[tuple[float, str]] = set()
    ordered: list[tuple[float, str]] = []
    for item in picked:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered[0]


def delay_damages_daily(
    rate_percent: float = 0.0,
    contract_amount: float = 0.0,
    currency: str = "SAR",
) -> dict:
    """Daily delay damages = rate% × Accepted Contract Amount / Contract Price.

    FIDIC Sub-Clause 8.8: the Contractor pays the rate stated in the
    Contract Data for every calendar day of delay. Live E1 is 0.1% of
    the net ACA. Operands are parameters — this function does not invent
    a rate or an amount.
    """
    pct = float(rate_percent)
    base = float(contract_amount)
    if pct < 0 or base < 0:
        return {"error": "rate_percent and contract_amount must be >= 0."}
    # Empty-args / unbound class: defaults are 0, 0. Emitting
    # "0% of SAR 0.00" looks like a successful rate and poisons the
    # turn (live Set3 E3). A genuine 0% rate against a real base is
    # still 0/day — only a missing contract amount is unbound.
    if base <= 0:
        return {
            "error": (
                "delay_damages_daily needs a contract_amount "
                "(unbound delay-damages / empty rates)."
            )
        }
    daily = round(base * (pct / 100.0), 2)
    cur = (currency or "SAR").strip().upper() or "SAR"
    return {
        "daily_amount": daily,
        "rate_percent": pct,
        "contract_amount": round(base, 2),
        "currency": cur,
        "per": "calendar day",
        "standard": "FIDIC Sub-Clause 8.8 (rate × Accepted Contract Amount)",
        "note": (
            f"{pct:g}% of {cur} {base:,.2f} = {cur} {daily:,.2f} "
            f"per calendar day."
        ),
    }


def format_delay_damages_daily_line(composed: dict) -> str:
    """User-facing one-liner for the composed daily figure."""
    cur = composed.get("currency") or "SAR"
    daily = float(composed["daily_amount"])
    pct = float(composed["rate_percent"])
    base = float(composed["contract_amount"])
    return (
        f"Delay damages for the whole of the Works are "
        f"{cur} {daily:,.2f} per calendar day "
        f"({pct:g}% of Accepted Contract Amount {cur} {base:,.2f})."
    )


def compose_delay_damages_daily_from_excerpts(
    query: str,
    excerpts: str,
) -> dict | None:
    """Compose rate × ACA from retrieved client text, or None.

    Returns None when the ask is not E1-shaped, the kill-switch is off,
    or either operand is missing — never invents a figure.
    """
    if not compose_delay_damages_daily_enabled():
        return None
    if not query_asks_delay_damages_daily_amount(query):
        return None
    rate = parse_delay_damages_rate_percent(excerpts)
    aca = parse_accepted_contract_amount(excerpts)
    if rate is None or aca is None:
        return None
    amount, currency = aca
    return delay_damages_daily(
        rate_percent=rate,
        contract_amount=amount,
        currency=currency,
    )


def answer_states_daily_amount(text: str, daily_amount: float) -> bool:
    """True when ``text`` already states the composed daily figure."""
    if not text:
        return False
    formatted = f"{daily_amount:,.2f}"
    compact = f"{daily_amount:.2f}"
    blob = text.replace(" ", "")
    return (
        formatted in text
        or compact in text
        or formatted.replace(",", "") in blob
    )


# ── Named percentage particulars (Set3 A7/A9) and % × ACA (E1) ─────────────
#
# Live a8498b3: Advance Payment / Limitation of Liability rows were in
# the excerpts (retrieval scores 54–88) and synthesis hung empty.
# Delay-damages already have composers; these two are the same class —
# a filled Contract Data percentage the question named. Kill-switch:
# COMPOSE_PERCENTAGE_OF_ACA=0 restores the empty-turn FAIL.

_PCT_PARTICULAR_SPECS = (
    (
        "advance payment",
        "Advance Payment",
        re.compile(r"(?i)advance\s+payment"),
    ),
    (
        "limitation of liability",
        "Limitation of Liability",
        re.compile(r"(?i)limitation\s+of\s+liability|limit(?:ation)?\s+of\s+liabilit"),
    ),
)
_PCT_BOND_RE = re.compile(r"(?i)\bbond\b|\bguarantee\b|\bsecurity\b")
_PCT_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONEY_ARITHMETIC_ASK_RE = re.compile(
    r"(?i)\b(?:calculate|compute|work\s+out|how\s+much)\b",
)
_MONEY_UNIT_ASK_RE = re.compile(
    r"(?i)\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b",
)
_EACH_DAYS_LATE_RE = re.compile(
    r"(?i)each\s+(\d+)\s*(?:calendar\s+|working\s+)?days?\s+"
    r"(?:late|of\s+delay|delay(?:ed)?|behind|overdue)",
)
_DAYS_LATE_RE = re.compile(
    r"(?i)(\d+)\s*(?:calendar\s+|working\s+)?days?\s+"
    r"(?:late|of\s+delay|delay(?:ed)?|behind|overdue)",
)
_MILESTONE_LIST_RE = re.compile(
    r"(?i)milestones?\s+(\d+(?:\s*(?:,|and|&)\s*\d+)+)",
)
_MILESTONE_ONE_RE = re.compile(r"(?i)milestone\s+(\d+)")
_MILESTONE_RATE_RE = re.compile(
    r"(?i)milestone\s+(\d+)\s*[|:]\s*"
    r"(\d+(?:\.\d+)?)\s*%\s+of\s+(?:the\s+)?"
    r"(?:contract\s+price|accepted\s+contract\s+amount)"
    r"[^.]{0,64}\bper\b",
)
_UNBOUND_DD_NOTE_RE = re.compile(
    r"(?i)0\s*%\s+of\s+(?:SAR|AED|USD|EUR|GBP|QAR|BHD|KWD|OMR)"
    r"\s+0(?:\.00)?",
)


def compose_percentage_of_aca_enabled() -> bool:
    raw = (os.getenv("COMPOSE_PERCENTAGE_OF_ACA", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def compose_delay_damages_period_enabled() -> bool:
    raw = (os.getenv("COMPOSE_DELAY_DAMAGES_PERIOD", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _asked_percentage_spec(query: str):
    q = query or ""
    for key, display, rx in _PCT_PARTICULAR_SPECS:
        if rx.search(q):
            return key, display, rx
    return None


def query_asks_named_percentage_particular(query: str) -> bool:
    """True for Advance Payment / Limitation of Liability lookups.

    Delay damages stay on their own composers. Definition questions
    ("what does Advance Payment mean") are not this class.
    """
    q = query or ""
    if not q:
        return False
    if _DD_ASK_RE.search(q):
        return False
    if re.search(r"(?i)\bwhat\s+does\b.{0,40}\bmean\b", q):
        return False
    return _asked_percentage_spec(q) is not None


def query_asks_percentage_particular_in_money(query: str) -> bool:
    """True for E1-shaped "Calculate the Advance Payment in SAR"."""
    if not query_asks_named_percentage_particular(query):
        return False
    q = query or ""
    return bool(_MONEY_ARITHMETIC_ASK_RE.search(q) and _MONEY_UNIT_ASK_RE.search(q))


def extract_named_percentage_particular(
    query: str,
    excerpts: str,
) -> dict | None:
    """Filled percentage for the named particular, or None.

    Does not invent a percentage. Skips an Advance Payment *Bond* row
    when the ask is the Advance Payment itself.
    """
    spec = _asked_percentage_spec(query)
    if not spec:
        return None
    _key, display, label_rx = spec
    t = excerpts or ""
    if not t:
        return None
    ask_wants_bond = bool(_PCT_BOND_RE.search(query or ""))

    def _from_pair(key: str, val: str) -> dict | None:
        blob = f"{key} {val}"
        if not label_rx.search(key) and not label_rx.search(val[:96]):
            return None
        if _PCT_BOND_RE.search(blob) and not ask_wants_bond:
            return None
        m = _PCT_VALUE_RE.search(val) or _PCT_VALUE_RE.search(key)
        if not m:
            return None
        return {
            "label": display,
            "percent": float(m.group(1)),
            "value": val.strip(),
            "key": key.strip(),
        }

    try:
        from app.core.contract_data_chunks import filled_particulars_rows
        for key, val in filled_particulars_rows(t):
            parsed = _from_pair(key, val)
            if parsed:
                return parsed
    except Exception:  # noqa: BLE001 — scanned fallback still runs
        logger.debug(
            "particulars percentage parse failed; using scanned regex",
            exc_info=True,
        )
    blob = _collapse_ws(t)
    for m in label_rx.finditer(blob):
        window = blob[m.start(): m.end() + 160]
        if _PCT_BOND_RE.search(window) and not ask_wants_bond:
            continue
        pct = _PCT_VALUE_RE.search(window)
        if pct:
            return {
                "label": display,
                "percent": float(pct.group(1)),
                "value": window.strip(),
                "key": display,
            }
    return None


def format_named_percentage_line(parsed: dict) -> str:
    pct = float(parsed["percent"])
    label = parsed.get("label") or "particular"
    return (
        f"The {label} is {pct:g}% of the Accepted Contract Amount."
    )


def compose_percentage_of_aca_from_excerpts(
    query: str,
    excerpts: str,
) -> dict | None:
    """Compose named-row % × ACA, or None. Never invents an operand."""
    if not compose_percentage_of_aca_enabled():
        return None
    if not query_asks_percentage_particular_in_money(query):
        return None
    parsed = extract_named_percentage_particular(query, excerpts)
    aca = parse_accepted_contract_amount(excerpts)
    if parsed is None or aca is None:
        return None
    amount, currency = aca
    product = round(amount * (float(parsed["percent"]) / 100.0), 2)
    return {
        "amount": product,
        "percent": float(parsed["percent"]),
        "contract_amount": round(amount, 2),
        "currency": (currency or "SAR").upper(),
        "label": parsed.get("label") or "particular",
    }


def format_percentage_of_aca_line(composed: dict) -> str:
    cur = composed.get("currency") or "SAR"
    amt = float(composed["amount"])
    pct = float(composed["percent"])
    base = float(composed["contract_amount"])
    label = composed.get("label") or "particular"
    return (
        f"The {label} is {cur} {amt:,.2f} "
        f"({pct:g}% of Accepted Contract Amount {cur} {base:,.2f})."
    )


def answer_states_money_amount(text: str, amount: float) -> bool:
    """True when ``text`` already states ``amount`` as written money."""
    if not text:
        return False
    formatted = f"{amount:,.2f}"
    compact = f"{amount:.2f}"
    blob = text.replace(" ", "")
    return (
        formatted in text
        or compact in text
        or formatted.replace(",", "") in blob
        or f"{amount:,.1f}" in text
    )


# ── Delay damages over a period (Set3 E3 / E2 class) ──────────────────────


def query_applies_a_delay_duration(query: str) -> bool:
    q = query or ""
    return bool(_EACH_DAYS_LATE_RE.search(q) or _DAYS_LATE_RE.search(q))


def query_asks_delay_damages_over_a_period(query: str) -> bool:
    """True for "M3 and M4 are each 20 days late … delay damages".

    A5 (rate lookup, no duration) and a per-calendar-day E1 with no
    delay period stay off this path.
    """
    q = query or ""
    if not q or not _DD_ASK_RE.search(q):
        return False
    return query_applies_a_delay_duration(q)


def parse_asked_milestones(query: str) -> list[int]:
    """Milestone numbers the question names, in order, de-duplicated."""
    q = query or ""
    nums: list[int] = []
    listed = _MILESTONE_LIST_RE.search(q)
    if listed:
        nums = [int(x) for x in re.findall(r"\d+", listed.group(1))]
    if not nums:
        nums = [int(x) for x in _MILESTONE_ONE_RE.findall(q)]
    out: list[int] = []
    seen: set[int] = set()
    for n in nums:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def parse_delay_period_days(query: str) -> int | None:
    m = _EACH_DAYS_LATE_RE.search(query or "") or _DAYS_LATE_RE.search(query or "")
    if not m:
        return None
    days = int(m.group(1))
    return days if days > 0 else None


def _window_is_whole_of_works_rate(blob: str) -> bool:
    """True when the window is the whole-of-Works daily rate, not a Milestone.

    Live Set3 E3 on 4ab5561: a packed particulars row said "per Milestone"
    and then "Delay Damages (for the whole of the Works): 0.1%". First-
    percent parse elected 0.1% and composed 70,180,178.25. The Contract
    Data milestone rate is 0.015%.
    """
    return bool(_WHOLE_WORKS_RE.search(blob or ""))


def parse_milestone_delay_rate_percent(
    excerpts: str,
    milestone: int,
) -> float | None:
    """Per-day % for one Milestone row. Does not invent; ignores the cap.

    Rejects a whole-of-Works 0.1% packed under a "per Milestone" label —
    that product is a different figure (live E3 vs 0.1% × 40).
    """
    t = excerpts or ""
    if not t or milestone <= 0:
        return None
    for m in _MILESTONE_RATE_RE.finditer(t):
        if int(m.group(1)) != milestone:
            continue
        window = t[max(0, m.start() - 96): m.end() + 48]
        if _window_is_whole_of_works_rate(window):
            continue
        return float(m.group(2))
    try:
        from app.core.contract_data_chunks import filled_particulars_rows
        for key, val in filled_particulars_rows(t):
            if _DD_CAP_KEY_RE.search(key):
                continue
            blob = f"{key} {val}"
            if not re.search(rf"(?i)milestone\s+{milestone}\b", blob):
                continue
            if _window_is_whole_of_works_rate(blob):
                continue
            if not re.search(r"(?i)\bper\b", val):
                continue
            pct = _PCT_VALUE_RE.search(val)
            if pct:
                return float(pct.group(1))
    except Exception:  # noqa: BLE001 — scanned regex already ran
        logger.debug("milestone rate particulars parse failed", exc_info=True)
    return None


def _shared_milestone_delay_rate(excerpts: str) -> float | None:
    """The per-Milestone rate when every listed row carries the same %."""
    rates: list[float] = []
    t = excerpts or ""
    for m in _MILESTONE_RATE_RE.finditer(t):
        window = t[max(0, m.start() - 96): m.end() + 48]
        if _window_is_whole_of_works_rate(window):
            continue
        rates.append(float(m.group(2)))
    if not rates:
        return None
    first = rates[0]
    if all(abs(r - first) < 1e-9 for r in rates):
        return first
    return None


def compose_delay_damages_over_period_from_excerpts(
    query: str,
    excerpts: str,
) -> dict | None:
    """Compose rate × ACA × days [× milestones], or None.

    Uses the Milestone row the question names. Does not fall through to
    the whole-of-Works 0.1% when the ask is a Milestone scenario — that
    product is a different figure (live E3 vs 0.1% × 40).
    """
    if not compose_delay_damages_period_enabled():
        return None
    if not query_asks_delay_damages_over_a_period(query):
        return None
    days = parse_delay_period_days(query)
    aca = parse_accepted_contract_amount(excerpts)
    if days is None or aca is None:
        return None
    amount, currency = aca
    milestones = parse_asked_milestones(query)
    rates: list[float] = []
    if milestones:
        shared = _shared_milestone_delay_rate(excerpts)
        for n in milestones:
            rate = parse_milestone_delay_rate_percent(excerpts, n)
            if rate is None:
                rate = shared
            if rate is None:
                return None
            rates.append(rate)
    else:
        rate = parse_delay_damages_rate_percent(excerpts)
        if rate is None:
            return None
        rates = [rate]
    total = round(sum(amount * (r / 100.0) * days for r in rates), 2)
    return {
        "amount": total,
        "days": days,
        "rates": rates,
        "milestones": milestones,
        "contract_amount": round(amount, 2),
        "currency": (currency or "SAR").upper(),
    }


def format_delay_damages_period_line(composed: dict) -> str:
    cur = composed.get("currency") or "SAR"
    amt = float(composed["amount"])
    days = int(composed["days"])
    base = float(composed["contract_amount"])
    rates = composed.get("rates") or []
    milestones = composed.get("milestones") or []
    rate_txt = ", ".join(f"{float(r):g}%" for r in rates) or "the stated rate"
    if milestones:
        ms = " and ".join(f"Milestone {n}" for n in milestones)
        return (
            f"Combined delay damages for {ms} "
            f"({days} days each) are {cur} {amt:,.2f} "
            f"({rate_txt} of Accepted Contract Amount {cur} {base:,.2f} "
            f"× {days} days × {len(milestones)})."
        )
    return (
        f"Delay damages for {days} days are {cur} {amt:,.2f} "
        f"({rate_txt} of Accepted Contract Amount {cur} {base:,.2f} "
        f"× {days} days)."
    )


def answer_is_unbound_delay_damages(text: str) -> bool:
    """True for the empty-args '0% of SAR 0.00' poison."""
    return bool(_UNBOUND_DD_NOTE_RE.search(text or ""))


# ── User-priced concrete take-off (Cost-gate A3-1) ─────────────────────────
# Live SO probe: construction_calc succeeded, force_synthesis emitted 0
# tokens, and the bubble stayed blank. Volume-only recover also fails
# the gate because the operator asked for SAR 410 + waste + contingency.
# Kill-switch: COMPOSE_USER_PRICED_TAKEOFF=0. Never invent a rate.

_PRICED_TAKEOFF_MATERIAL_RE = re.compile(
    r"(?i)\b(concrete|footing|pad\s+footing|raft|slab)\b",
)
_PRICED_TAKEOFF_VERB_RE = re.compile(
    r"(?i)\b(take\s*off|volume|price|priced|cost)\b",
)
_USER_UNIT_RATE_RE = re.compile(
    r"(?i)\b(SAR|USD|AED|QAR|EUR|GBP)\s*([\d,]+(?:\.\d+)?)\s*"
    r"(?:per|/)\s*m(?:³|3)\b",
)
_FOOTING_COUNT_RE = re.compile(
    r"(?i)(\d+)\s+(?:pad\s+)?footings?\b",
)
_LWT_CHAIN_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d[\d,]*(?:\.\d+)?)\s*[x×*]\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*[x×*]\s*"
    r"(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:mm|cm|m)\b)?",
    re.IGNORECASE,
)
_WASTE_PCT_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%\s*waste")
_CONTINGENCY_PCT_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%\s*contingenc")


def compose_user_priced_takeoff_enabled() -> bool:
    raw = (os.getenv("COMPOSE_USER_PRICED_TAKEOFF", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def query_asks_user_priced_takeoff(query: str) -> bool:
    """True for concrete take-off + an operator-instructed unit rate."""
    q = query or ""
    if not q:
        return False
    if not _PRICED_TAKEOFF_MATERIAL_RE.search(q):
        return False
    if not _PRICED_TAKEOFF_VERB_RE.search(q):
        return False
    return bool(_USER_UNIT_RATE_RE.search(q))


def _parse_lwt_metres(text: str) -> tuple[float, float, float] | None:
    match = _LWT_CHAIN_RE.search(text or "")
    if not match:
        return None
    return tuple(float(g.replace(",", "")) for g in match.groups())  # type: ignore[return-value]


def _parse_footing_count(text: str) -> int:
    match = _FOOTING_COUNT_RE.search(text or "")
    if not match:
        return 1
    n = int(match.group(1))
    return n if n > 0 else 1


def _parse_user_unit_rate(text: str) -> tuple[float, str] | None:
    match = _USER_UNIT_RATE_RE.search(text or "")
    if not match:
        return None
    return float(match.group(2).replace(",", "")), match.group(1).upper()


def _format_qty(value: float) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def compose_user_priced_takeoff_from_ask(text: str) -> dict | None:
    """Volume × user rate × stated waste/contingency, or None.

    Operands come from the question only. A missing unit rate is not
    filled in. Kill-switch ``COMPOSE_USER_PRICED_TAKEOFF=0`` returns None.
    """
    if not compose_user_priced_takeoff_enabled():
        return None
    if not query_asks_user_priced_takeoff(text):
        return None
    dims = _parse_lwt_metres(text)
    rate = _parse_user_unit_rate(text)
    if dims is None or rate is None:
        return None
    length, width, depth = dims
    unit_rate, currency = rate
    if unit_rate <= 0 or min(length, width, depth) <= 0:
        return None
    count = _parse_footing_count(text)
    net = count * length * width * depth
    waste_m = _WASTE_PCT_RE.search(text or "")
    contingency_m = _CONTINGENCY_PCT_RE.search(text or "")
    waste_pct = float(waste_m.group(1)) if waste_m else 0.0
    contingency_pct = float(contingency_m.group(1)) if contingency_m else 0.0
    if waste_pct < 0 or waste_pct > 100 or contingency_pct < 0 or contingency_pct > 100:
        return None
    with_waste = net * (1.0 + waste_pct / 100.0)
    base_cost = round(with_waste * unit_rate, 2)
    total_cost = round(base_cost * (1.0 + contingency_pct / 100.0), 2)
    return {
        "count": count,
        "length": length,
        "width": width,
        "depth": depth,
        "net_volume_m3": net,
        "volume_with_waste_m3": with_waste,
        "waste_percent": waste_pct,
        "contingency_percent": contingency_pct,
        "unit_rate": unit_rate,
        "currency": currency,
        "base_cost": base_cost,
        "total_cost": total_cost,
    }


def format_user_priced_takeoff_line(composed: dict) -> str:
    """User-facing A3-1 line from ``compose_user_priced_takeoff_from_ask``."""
    cur = composed.get("currency") or "SAR"
    count = int(composed["count"])
    length = float(composed["length"])
    width = float(composed["width"])
    depth = float(composed["depth"])
    net = float(composed["net_volume_m3"])
    with_waste = float(composed["volume_with_waste_m3"])
    waste_pct = float(composed.get("waste_percent") or 0.0)
    contingency_pct = float(composed.get("contingency_percent") or 0.0)
    rate = float(composed["unit_rate"])
    base = float(composed["base_cost"])
    total = float(composed["total_cost"])
    parts = [
        f"Net volume {count} × {_format_qty(length)} × {_format_qty(width)} "
        f"× {_format_qty(depth)} = {_format_qty(net)} m3.",
    ]
    if waste_pct:
        parts.append(
            f"With {waste_pct:g}% waste: {_format_qty(with_waste)} m3."
        )
    parts.append(
        f"At the instructed {cur} {_format_qty(rate)}/m3 = {cur} {base:,.2f}."
    )
    if contingency_pct:
        parts.append(
            f"Plus {contingency_pct:g}% contingency: {cur} {total:,.2f}."
        )
    return " ".join(parts)


ADDITIONAL_CALCULATORS = {
    "roi_calculator": roi_calculator,
    "unit_cost_total": unit_cost_total,
    "cost_per_area": cost_per_area,
    "productivity_rate": productivity_rate,
    "delay_damages_daily": delay_damages_daily,
}
