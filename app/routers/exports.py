"""Schedule + report export endpoints — xlsx / docx / pdf.

Takes a list of activity dicts (the shape `generate_wbs` returns in its
`activities` field) and renders them to a file the user can download. The
schedule export wraps openpyxl directly (works without the existing
`write_schedule_excel` helper because that helper expects a CPMOutput
dataclass instance — these endpoints accept the plain dicts that the
agent runtime emits).

DOCX rendering uses python-docx; PDF rendering uses reportlab if
installed and otherwise falls back to a docx-as-pdf fallback (which on
this image is itself unavailable, so PDF returns 503 when reportlab is
absent).
"""
from __future__ import annotations

import io
import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.dependencies import require_user
from app.core import projects as projects_store

router = APIRouter()


class ScheduleExportRequest(BaseModel):
    """Inline activity list. Each activity is a dict with at least
    `id`, `name`, `duration_days`, `early_start_day`, `early_finish_day`,
    `total_float_days`, `critical`, `wbs_phase`, `predecessors`.
    """
    activities: List[Dict[str, Any]] = Field(..., description="generate_wbs-style activity rows")
    project_name: Optional[str] = None
    start_date: Optional[str] = None  # ISO YYYY-MM-DD
    end_date: Optional[str] = None
    notes: Optional[str] = None


def _check_owner(project_id: str, user_id: str) -> Dict[str, Any]:
    proj = projects_store.get_project(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")
    return proj


def _render_xlsx(activities: List[Dict[str, Any]], project_name: str,
                 start_date: Optional[str], notes: Optional[str]) -> str:
    """Write activities to a temp xlsx and return its path."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Schedule"

    # Header banner
    ws["A1"] = project_name or "Schedule"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Start: {start_date or ''}    Activities: {len(activities)}"
    ws["A2"].font = Font(italic=True)

    # Column headers
    headers = ["ID", "Phase", "Name", "Duration (days)", "ES day", "EF day",
               "Total Float", "Critical", "Predecessors", "Resources"]
    for ci, h in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=ci, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.alignment = Alignment(horizontal="center")

    crit_fill = PatternFill("solid", fgColor="F8CBAD")
    for ri, a in enumerate(activities, start=5):
        critical = bool(a.get("critical"))
        row = [
            a.get("id") or a.get("code") or "",
            a.get("wbs_phase") or a.get("phase") or "",
            a.get("name") or "",
            a.get("duration_days") or a.get("duration") or 0,
            a.get("early_start_day") if a.get("early_start_day") is not None else "",
            a.get("early_finish_day") if a.get("early_finish_day") is not None else "",
            a.get("total_float_days") if a.get("total_float_days") is not None else "",
            "YES" if critical else "",
            ", ".join(a.get("predecessors") or []),
            ", ".join(a.get("resources") or []),
        ]
        for ci, v in enumerate(row, start=1):
            cell = ws.cell(row=ri, column=ci, value=v)
            if critical:
                cell.fill = crit_fill

    # Column widths
    widths = [10, 22, 40, 14, 10, 10, 12, 10, 28, 22]
    for ci, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + ci)].width = w

    if notes:
        notes_row = 5 + len(activities) + 2
        ws.cell(row=notes_row, column=1, value="Notes:").font = Font(bold=True)
        ws.cell(row=notes_row + 1, column=1, value=notes)

    fd, path = tempfile.mkstemp(prefix="schedule_", suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def _render_docx(activities: List[Dict[str, Any]], project_name: str,
                 start_date: Optional[str], notes: Optional[str]) -> str:
    """Write activities to a temp .docx schedule report and return its path."""
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.table import WD_ALIGN_VERTICAL

    doc = Document()
    doc.add_heading(project_name or "Schedule", level=0)
    meta = doc.add_paragraph()
    meta.add_run(f"Start: {start_date or '—'}    Activities: {len(activities)}").italic = True

    table = doc.add_table(rows=1, cols=8)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for ci, h in enumerate(
        ["ID", "Phase", "Name", "Dur.", "ES", "EF", "Float", "Crit."]
    ):
        hdr[ci].text = h
        for p in hdr[ci].paragraphs:
            for r in p.runs:
                r.bold = True

    for a in activities:
        row = table.add_row().cells
        row[0].text = str(a.get("id") or a.get("code") or "")
        row[1].text = str(a.get("wbs_phase") or a.get("phase") or "")
        row[2].text = str(a.get("name") or "")
        row[3].text = str(a.get("duration_days") or a.get("duration") or "")
        row[4].text = str(a.get("early_start_day") if a.get("early_start_day") is not None else "")
        row[5].text = str(a.get("early_finish_day") if a.get("early_finish_day") is not None else "")
        row[6].text = str(a.get("total_float_days") if a.get("total_float_days") is not None else "")
        row[7].text = "YES" if a.get("critical") else ""

    if notes:
        doc.add_paragraph()
        nh = doc.add_paragraph()
        nh.add_run("Notes").bold = True
        doc.add_paragraph(notes)

    fd, path = tempfile.mkstemp(prefix="schedule_", suffix=".docx")
    os.close(fd)
    doc.save(path)
    return path


def _render_pdf(activities: List[Dict[str, Any]], project_name: str,
                start_date: Optional[str], notes: Optional[str]) -> str:
    """Write activities to a temp PDF using reportlab. Raises 503 if absent."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="PDF export unavailable — reportlab is not installed in this image. "
                   "Uncomment reportlab in requirements.txt and redeploy to enable.",
        )

    fd, path = tempfile.mkstemp(prefix="schedule_", suffix=".pdf")
    os.close(fd)
    pdf = SimpleDocTemplate(path, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(project_name or "Schedule", styles["Title"]),
        Paragraph(
            f"Start: {start_date or '—'}    Activities: {len(activities)}",
            styles["Italic"],
        ),
        Spacer(1, 12),
    ]
    data = [["ID", "Phase", "Name", "Dur.", "ES", "EF", "Float", "Crit."]]
    for a in activities:
        data.append([
            str(a.get("id") or a.get("code") or ""),
            str(a.get("wbs_phase") or a.get("phase") or ""),
            (str(a.get("name") or ""))[:60],
            str(a.get("duration_days") or a.get("duration") or ""),
            str(a.get("early_start_day") if a.get("early_start_day") is not None else ""),
            str(a.get("early_finish_day") if a.get("early_finish_day") is not None else ""),
            str(a.get("total_float_days") if a.get("total_float_days") is not None else ""),
            "YES" if a.get("critical") else "",
        ])
    t = Table(data, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for r, row in enumerate(data):
        if r > 0 and row[7] == "YES":
            style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F8CBAD")))
    t.setStyle(TableStyle(style))
    elements.append(t)
    if notes:
        elements += [Spacer(1, 12), Paragraph("<b>Notes</b>", styles["Normal"]),
                     Paragraph(notes, styles["Normal"])]
    pdf.build(elements)
    return path


@router.post("/v1/projects/{project_id}/export/schedule")
async def export_schedule(
    project_id: str,
    req: ScheduleExportRequest,
    format: str = "xlsx",
    auth: Dict[str, Any] = Depends(require_user),
):
    """Export an activity list to xlsx, docx, or pdf.

    `format` query param: `xlsx` (default), `docx`, or `pdf`.
    Body: `{"activities": [...], "project_name": "...", "start_date": "...", "notes": "..."}`.
    Returns the file via FileResponse — the browser triggers a download.
    """
    proj = _check_owner(project_id, auth["user_id"])
    if not req.activities:
        raise HTTPException(400, "activities is required and must be non-empty")
    name = req.project_name or proj.get("name") or "Schedule"

    fmt = (format or "xlsx").lower()
    if fmt == "xlsx":
        path = _render_xlsx(req.activities, name, req.start_date, req.notes)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    elif fmt == "docx":
        path = _render_docx(req.activities, name, req.start_date, req.notes)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ext = "docx"
    elif fmt == "pdf":
        path = _render_pdf(req.activities, name, req.start_date, req.notes)
        media = "application/pdf"
        ext = "pdf"
    else:
        raise HTTPException(400, f"Unsupported format '{format}'. Use xlsx, docx, or pdf.")

    download_name = f"{name.replace(' ', '_')}_schedule.{ext}"
    return FileResponse(path, media_type=media, filename=download_name)
