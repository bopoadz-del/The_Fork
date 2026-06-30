"""Cost-loaded L2 schedule + EVM workbook generators (formula-linked).

Matches the Anthropic / Kenya 200MW / Canada 1GW L2 exemplars:
  L2 Schedule        - CPM (ES/EF/LS/LF/float/critical) + cost per activity
  Cost Loading       - cost baseline / S-curve with cumulative =prev+curr
  Manpower Histogram - man-days = =Dur*Manpower
  Summary            - links to total cost, duration, critical count

EVM workbook (from PV/EV/AC + BAC):
  CV=EV-AC, SV=EV-PV, CPI=EV/AC, SPI=EV/PV, EAC=BAC/CPI, ETC=EAC-AC, VAC=BAC-EAC
  — all LIVE Excel formulas, so the client can audit performance, not pasted.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

_HDR_FILL = PatternFill("solid", fgColor="122B49")
_HDR_FONT = Font(bold=True, color="FFFFFF")
_TITLE = Font(bold=True, size=16)
_BOLD = Font(bold=True)
_INPUT = Font(color="0000FF")   # blue = inputs
_XREF = Font(color="008000")    # green = cross-sheet
_MONEY = "#,##0.00"


def _header(ws, row: int, labels: List[str]) -> None:
    for j, h in enumerate(labels, start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.font, c.fill, c.alignment = _HDR_FONT, _HDR_FILL, Alignment(horizontal="center")


def _cpm(activities: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, int]], int, List[str]]:
    """Forward/backward CPM pass (finish-to-start). Returns
    ({id: {es,ef,ls,lf,tf,critical}}, project_duration, topo_order)."""
    acts = {str(a["id"]): a for a in activities}
    ids = [str(a["id"]) for a in activities]
    preds = {i: [str(p) for p in acts[i].get("predecessors", [])] for i in ids}
    succ: Dict[str, List[str]] = {i: [] for i in ids}
    for i in ids:
        for p in preds[i]:
            if p in succ:
                succ[p].append(i)
    indeg = {i: sum(1 for p in preds[i] if p in acts) for i in ids}
    q = deque([i for i in ids if indeg[i] == 0])
    order: List[str] = []
    while q:
        n = q.popleft(); order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    if len(order) != len(ids):          # cycle fallback — keep input order
        order = ids
    es: Dict[str, int] = {}; ef: Dict[str, int] = {}
    for i in order:
        es[i] = max([ef[p] for p in preds[i] if p in ef], default=0)
        ef[i] = es[i] + int(acts[i].get("duration", 0))
    proj = max(ef.values(), default=0)
    ls: Dict[str, int] = {}; lf: Dict[str, int] = {}
    for i in reversed(order):
        lf[i] = min([ls[s] for s in succ[i] if s in ls], default=proj)
        ls[i] = lf[i] - int(acts[i].get("duration", 0))
    out = {i: {"es": es[i], "ef": ef[i], "ls": ls[i], "lf": lf[i],
               "tf": ls[i] - es[i], "critical": (ls[i] - es[i]) <= 0} for i in ids}
    return out, proj, order


def generate_cost_loaded_schedule(meta: Dict[str, Any], activities: List[Dict[str, Any]]):
    cpm, proj, order = _cpm(activities)
    acts = {str(a["id"]): a for a in activities}
    ccy = meta.get("currency", "SAR")
    wb = openpyxl.Workbook()

    # ── L2 Schedule ─────────────────────────────────────────────────────────
    s = wb.active; s.title = "L2 Schedule"
    s["A1"] = f"L2 SCHEDULE — {meta.get('project', '')}"; s["A1"].font = _TITLE
    _header(s, 3, ["ID", "WBS", "Activity", "Dur", "Preds", "ES", "EF", "LS", "LF",
                   "Float", "Critical", f"Cost ({ccy})"])
    r = 4
    first = r
    for i in order:
        a, c = acts[i], cpm[i]
        s.cell(r, 1, i); s.cell(r, 2, a.get("wbs", "")); s.cell(r, 3, a.get("name", ""))
        s.cell(r, 4, a.get("duration", 0)); s.cell(r, 5, ",".join(str(p) for p in a.get("predecessors", [])))
        s.cell(r, 6, c["es"]); s.cell(r, 7, c["ef"]); s.cell(r, 8, c["ls"]); s.cell(r, 9, c["lf"])
        s.cell(r, 10, c["tf"]); s.cell(r, 11, "YES" if c["critical"] else "")
        cc = s.cell(r, 12, a.get("cost", 0)); cc.font = _INPUT; cc.number_format = _MONEY
        r += 1
    s.cell(r, 3, "TOTAL").font = _BOLD
    tc = s.cell(r, 12, f"=SUM(L{first}:L{r - 1})"); tc.font = _BOLD; tc.number_format = _MONEY
    total_cost_row = r
    s.cell(r + 2, 3, "Project Duration (days)").font = _BOLD
    s.cell(r + 2, 4, proj).font = _BOLD

    # ── Cost Loading (cumulative S-curve) ───────────────────────────────────
    cl = wb.create_sheet("Cost Loading")
    cl["A1"] = "COST LOADING / BASELINE (S-CURVE)"; cl["A1"].font = _TITLE
    _header(cl, 3, ["ID", "Activity", f"Cost ({ccy})", f"Cumulative ({ccy})"])
    rr = 4
    for idx, i in enumerate(order):
        cl.cell(rr, 1, i); cl.cell(rr, 2, acts[i].get("name", ""))
        lk = cl.cell(rr, 3, f"='L2 Schedule'!L{first + idx}"); lk.font = _XREF; lk.number_format = _MONEY
        cum = f"=C{rr}" if idx == 0 else f"=D{rr - 1}+C{rr}"   # live cumulative
        cl.cell(rr, 4, cum).number_format = _MONEY
        rr += 1

    # ── Manpower Histogram ──────────────────────────────────────────────────
    mp = wb.create_sheet("Manpower Histogram")
    mp["A1"] = "MANPOWER HISTOGRAM"; mp["A1"].font = _TITLE
    _header(mp, 3, ["ID", "Activity", "Dur", "Manpower", "Man-days"])
    rr = 4
    for i in order:
        a = acts[i]
        mp.cell(rr, 1, i); mp.cell(rr, 2, a.get("name", ""))
        mp.cell(rr, 3, a.get("duration", 0)).font = _INPUT
        mp.cell(rr, 4, a.get("manpower", 0)).font = _INPUT
        mp.cell(rr, 5, f"=C{rr}*D{rr}").number_format = "#,##0"   # man-days = Dur*Manpower
        rr += 1
    mp.cell(rr, 2, "TOTAL").font = _BOLD
    mp.cell(rr, 5, f"=SUM(E4:E{rr - 1})").font = _BOLD

    # ── Summary ─────────────────────────────────────────────────────────────
    summ = wb.create_sheet("Summary")
    summ["A1"] = f"SCHEDULE SUMMARY — {meta.get('project', '')}"; summ["A1"].font = _TITLE
    summ["B3"] = "Project Duration (days)"; summ["C3"] = f"='L2 Schedule'!D{total_cost_row + 2}"
    summ["B4"] = f"Total Cost ({ccy})"
    tcl = summ["C4"]; tcl.value = f"='L2 Schedule'!L{total_cost_row}"; tcl.font = _XREF; tcl.number_format = _MONEY
    summ["B5"] = "Critical Activities"
    summ["C5"] = sum(1 for i in order if cpm[i]["critical"])
    summ["B6"] = "Total Man-days"; summ["C6"] = f"='Manpower Histogram'!E{rr}"
    summ.sheet_view.showGridLines = False
    return wb


def generate_evm_workbook(meta: Dict[str, Any], periods: List[Dict[str, Any]]):
    """EVM workbook: PV/EV/AC inputs per period; CV/SV/CPI/SPI/EAC/ETC/VAC as
    live formulas. BAC drives EAC/VAC."""
    bac = meta.get("bac", 0)
    ccy = meta.get("currency", "SAR")
    wb = openpyxl.Workbook()
    e = wb.active; e.title = "EVM"
    e["A1"] = f"EARNED VALUE MANAGEMENT — {meta.get('project', '')}"; e["A1"].font = _TITLE
    e["A2"] = "BAC (Budget at Completion):"
    bc = e["B2"]; bc.value = bac; bc.font = _INPUT; bc.number_format = _MONEY
    _header(e, 4, ["Period", f"PV ({ccy})", f"EV ({ccy})", f"AC ({ccy})",
                   "CV (EV-AC)", "SV (EV-PV)", "CPI (EV/AC)", "SPI (EV/PV)",
                   f"EAC ({ccy})", f"ETC ({ccy})", f"VAC ({ccy})"])
    r = 5
    for p in periods:
        e.cell(r, 1, p.get("period", ""))
        e.cell(r, 2, p.get("pv", 0)).font = _INPUT
        e.cell(r, 3, p.get("ev", 0)).font = _INPUT
        e.cell(r, 4, p.get("ac", 0)).font = _INPUT
        e.cell(r, 5, f"=C{r}-D{r}").number_format = _MONEY          # CV = EV - AC
        e.cell(r, 6, f"=C{r}-B{r}").number_format = _MONEY          # SV = EV - PV
        e.cell(r, 7, f"=C{r}/D{r}").number_format = "0.000"        # CPI = EV / AC
        e.cell(r, 8, f"=C{r}/B{r}").number_format = "0.000"        # SPI = EV / PV
        e.cell(r, 9, f"=$B$2/G{r}").number_format = _MONEY          # EAC = BAC / CPI
        e.cell(r, 10, f"=I{r}-D{r}").number_format = _MONEY         # ETC = EAC - AC
        e.cell(r, 11, f"=$B$2-I{r}").number_format = _MONEY         # VAC = BAC - EAC
        for col in (2, 3, 4):
            e.cell(r, col).number_format = _MONEY
        r += 1
    e.sheet_view.showGridLines = False
    return wb
