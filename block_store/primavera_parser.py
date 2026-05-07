"""Primavera Parser Block - Parse Oracle Primavera P6 XER schedule files"""

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


class PrimaveraParserBlock(UniversalBlock):
    name = "primavera_parser"
    version = "1.0.0"
    description = "Parse Primavera P6 .xer schedule files: critical path, milestones, resource loading"
    layer = 3
    tags = ["domain", "construction", "schedule", "primavera", "xer", "cpm"]
    requires = []

    default_config = {
        "include_resources": True,
        "critical_float_days": 0,
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".xer"],
            "placeholder": "Upload Primavera P6 .xer schedule file...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "activity_count", "type": "number", "label": "Activities"},
                {"name": "critical_path", "type": "list", "label": "Critical Path"},
                {"name": "milestones", "type": "list", "label": "Milestones"},
                {"name": "schedule_data", "type": "json", "label": "Schedule Summary"},
            ],
        },
        "quick_actions": [
            {"icon": "🗓️", "label": "Critical Path", "prompt": "Show the critical path activities"},
            {"icon": "🏁", "label": "Milestones", "prompt": "List all project milestones"},
            {"icon": "📊", "label": "Resource Loading", "prompt": "Show resource loading by period"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        file_path = data.get("file_path") or params.get("file_path")
        if not file_path:
            return {"status": "error", "error": "No file_path provided"}
        if not os.path.exists(file_path):
            return {"status": "error", "error": f"File not found: {file_path}"}
        if not file_path.lower().endswith(".xer"):
            return {"status": "error", "error": "File must be a .xer Primavera export"}

        try:
            tables = self._parse_xer(file_path)
        except Exception as e:
            return {"status": "error", "error": f"XER parse error: {e}"}

        float_threshold = int(
            params.get("critical_float_days", self.config.get("critical_float_days", 0))
        )
        include_resources = params.get(
            "include_resources", self.config.get("include_resources", True)
        )

        activities = self._extract_activities(tables)
        wbs = self._extract_wbs(tables)
        resources = self._extract_resources(tables) if include_resources else []
        project_meta = self._extract_project(tables)

        critical_path = [
            a for a in activities
            if a.get("total_float_days", 999) <= float_threshold
        ]
        milestones = [
            a for a in activities
            if a.get("type") in ("TT_Mile", "TT_FinMile", "TT_StartMile", "milestone")
        ]

        # Project date range
        start_dates = [a["start"] for a in activities if a.get("start")]
        end_dates = [a["finish"] for a in activities if a.get("finish")]
        project_start = min(start_dates) if start_dates else None
        project_finish = max(end_dates) if end_dates else None

        schedule_data = {
            "project": project_meta,
            "activity_count": len(activities),
            "wbs_count": len(wbs),
            "resource_count": len(resources),
            "project_start": project_start,
            "project_finish": project_finish,
            "critical_activity_count": len(critical_path),
            "milestone_count": len(milestones),
        }

        return {
            "status": "success",
            "schedule_data": schedule_data,
            "critical_path": critical_path[:100],
            "milestones": milestones[:50],
            "activities": activities[:200],
            "wbs": wbs[:100],
            "resources": resources[:50],
            "activity_count": len(activities),
        }

    def _parse_xer(self, file_path: str) -> Dict[str, List[Dict]]:
        """Parse XER tab-delimited table format into dict of table_name → rows."""
        tables: Dict[str, List[Dict]] = {}
        current_table: Optional[str] = None
        headers: List[str] = []

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("%T"):
                    current_table = line[3:].strip()
                    tables[current_table] = []
                    headers = []
                elif line.startswith("%F"):
                    headers = line[3:].split("\t")
                elif line.startswith("%R") and current_table and headers:
                    values = line[3:].split("\t")
                    row = dict(zip(headers, values + [""] * max(0, len(headers) - len(values))))
                    tables[current_table].append(row)
        return tables

    def _extract_activities(self, tables: Dict) -> List[Dict]:
        rows = tables.get("TASK", [])
        activities = []
        for r in rows:
            tf = r.get("total_float_hr_cnt", "")
            try:
                float_days = float(tf) / 8.0 if tf else 999
            except ValueError:
                float_days = 999

            activities.append({
                "id": r.get("task_id", ""),
                "code": r.get("task_code", ""),
                "name": r.get("task_name", ""),
                "type": r.get("task_type", ""),
                "status": r.get("status_code", ""),
                "start": _parse_date(r.get("act_start_date") or r.get("early_start_date", "")),
                "finish": _parse_date(r.get("act_end_date") or r.get("early_end_date", "")),
                "original_duration_days": _to_float(r.get("orig_dur", "0")) / 8,
                "remaining_duration_days": _to_float(r.get("remain_drtn_hr_cnt", "0")) / 8,
                "total_float_days": round(float_days, 1),
                "percent_complete": _to_float(r.get("phys_complete_pct", "0")),
                "wbs_id": r.get("wbs_id", ""),
            })
        return sorted(activities, key=lambda x: x.get("start") or "9999")

    def _extract_wbs(self, tables: Dict) -> List[Dict]:
        rows = tables.get("PROJWBS", [])
        return [
            {
                "id": r.get("wbs_id", ""),
                "code": r.get("wbs_short_name", ""),
                "name": r.get("wbs_name", ""),
                "parent_id": r.get("parent_wbs_id", ""),
                "level": int(_to_float(r.get("seq_num", "0"))),
            }
            for r in rows
        ]

    def _extract_resources(self, tables: Dict) -> List[Dict]:
        rows = tables.get("RSRC", [])
        return [
            {
                "id": r.get("rsrc_id", ""),
                "name": r.get("rsrc_name", ""),
                "short_name": r.get("rsrc_short_name", ""),
                "type": r.get("rsrc_type", ""),
                "unit_type": r.get("unit_id", ""),
            }
            for r in rows
        ]

    def _extract_project(self, tables: Dict) -> Dict:
        rows = tables.get("PROJECT", [])
        if not rows:
            return {}
        r = rows[0]
        return {
            "id": r.get("proj_id", ""),
            "name": r.get("proj_short_name", ""),
            "planned_start": _parse_date(r.get("plan_start_date", "")),
            "must_finish": _parse_date(r.get("scd_end_date", "")),
            "status": r.get("status_code", ""),
        }


def _parse_date(val: str) -> Optional[str]:
    if not val or val.strip() == "":
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%b-%y %H:%M", "%d-%b-%y"):
        try:
            return datetime.strptime(val.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val.strip() or None


def _to_float(val: str) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0
