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
# Live leftover E1 on c5c6dfa: Contract Data 8.8 chunks 9–11 carried a
# FIDIC worked-example ACA of SAR 10,000,000. Compose elected it
# (0.1% → SAR 10,000/day) instead of the filled excl-VAT row
# (~SAR 1,754,504,456.25). Kill-switch COMPOSE_REJECT_E1_TOY_ACA=0
# restores electing the first match (the FAIL).
_TOY_ACA_AMOUNT = 10_000_000.0
_EXAMPLE_ACA_RE = re.compile(
    r"(?i)\b(?:e\.g\.|eg\.|for\s+example|for\s+instance|"
    r"illustrative|worked\s+example|say\s+|insert\b|"
    r"placeholder|specimen|sample\s+amount|"
    r"if\s+the\s+accepted\s+contract\s+amount\s+is)\b"
)
_CLAUSE_111_RE = re.compile(r"(?i)\b1\.1\.1\b")


def compose_delay_damages_daily_enabled() -> bool:
    """ON by default. ``COMPOSE_DELAY_DAMAGES_DAILY=0`` is the kill-switch."""
    raw = (os.getenv("COMPOSE_DELAY_DAMAGES_DAILY", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def reject_e1_toy_aca_enabled() -> bool:
    """ON by default. ``COMPOSE_REJECT_E1_TOY_ACA=0`` restores the 10M FAIL."""
    raw = (os.getenv("COMPOSE_REJECT_E1_TOY_ACA", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def aca_amount_is_toy_example(amount: float, ctx: str = "") -> bool:
    """True for a FIDIC worked-example / placeholder ACA, not a filled row.

    Live E1: unlabeled ``SAR 10,000,000`` in an 8.8 window. A filled
    1.1.1 excluding-VAT particular of exactly 10M is not a toy.
    """
    if not reject_e1_toy_aca_enabled():
        return False
    ctx = ctx or ""
    if _EXAMPLE_ACA_RE.search(ctx):
        return True
    if abs(float(amount) - _TOY_ACA_AMOUNT) > 0.005:
        return False
    if _CLAUSE_111_RE.search(ctx) and _EXCL_VAT_RE.search(ctx):
        return False
    return True


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
    source = buckets if any_real else toys
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
