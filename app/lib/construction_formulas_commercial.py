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
    net = g - c
    roi = (net / c * 100.0) if c else 0.0
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
    rate = (t / a) if a else 0.0
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
    rate = (o / h) if h else 0.0
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


def compose_delay_damages_daily_enabled() -> bool:
    """ON by default. ``COMPOSE_DELAY_DAMAGES_DAILY=0`` is the kill-switch."""
    raw = (os.getenv("COMPOSE_DELAY_DAMAGES_DAILY", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def query_asks_delay_damages_daily_amount(query: str) -> bool:
    """True for E1 (calculate … delay damages … in SAR), not A5 rate lookup.

    Reuses the monetary-base ask class so A5 ("What are the Delay
    Damages…") stays a particular lookup and this path stays compose-only.
    """
    q = query or ""
    if not q or not _DD_ASK_RE.search(q):
        return False
    try:
        from app.core.rag.retriever import query_needs_a_monetary_base
    except Exception:  # noqa: BLE001 — never break a turn over an import
        return False
    return bool(query_needs_a_monetary_base(q))


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_delay_damages_rate_percent(text: str) -> float | None:
    """Daily Delay Damages *rate* as a percentage, or None.

    A cap row (``Maximum amount of delay damages: 10%…``) and a General
    Conditions pointer are not the rate. Does not invent a percentage.
    """
    t = text or ""
    if not t:
        return None
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
                return float(m.group(1))
    except Exception:  # noqa: BLE001 — fall through to the scanned regex
        logger.debug("particulars rate parse failed; using scanned regex", exc_info=True)
    blob = _collapse_ws(t)
    if _POINTER_RE.search(blob) and not _DD_RATE_PCT_RE.search(blob):
        return None
    m = _DD_RATE_PCT_RE.search(blob)
    if m:
        return float(m.group(1))
    for m in _DD_RATE_NEAR_LABEL_RE.finditer(blob):
        window = blob[max(0, m.start() - 48):m.end()]
        if _DD_CAP_KEY_RE.search(window):
            continue
        return float(m.group(1))
    return None


def parse_accepted_contract_amount(text: str) -> tuple[float, str] | None:
    """ACA / Contract Price money amount from client text, or None.

    Prefers an excluding-VAT figure when both incl/excl are present
    (FIDIC Contract Price / the rate's base is the net amount). Does
    not invent a figure; a percentage-of-ACA cap row is skipped.
    """
    t = text or ""
    if not t:
        return None
    excl: list[tuple[float, str]] = []
    neutral: list[tuple[float, str]] = []
    incl: list[tuple[float, str]] = []

    def _bucket(amount: float, currency: str, ctx: str) -> None:
        if amount < 1000:
            return
        if _DD_ASK_RE.search(ctx) and "%" in ctx:
            return
        if not _ACA_LABEL_RE.search(ctx):
            return
        item = (amount, currency)
        if _EXCL_VAT_RE.search(ctx):
            excl.append(item)
        elif _INCL_VAT_RE.search(ctx):
            incl.append(item)
        else:
            neutral.append(item)

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

    picked = excl or neutral or incl
    if not picked:
        return None
    # Dedup while preferring the first match in the preferred bucket.
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


ADDITIONAL_CALCULATORS = {
    "roi_calculator": roi_calculator,
    "unit_cost_total": unit_cost_total,
    "cost_per_area": cost_per_area,
    "productivity_rate": productivity_rate,
    "delay_damages_daily": delay_damages_daily,
}
