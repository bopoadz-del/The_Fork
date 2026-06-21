"""BOQ Processor Block - Parse Excel/CSV/PDF Bills of Quantities into structured line items"""

import logging
import os
from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock

_logger = logging.getLogger(__name__)


def _resolve_via_project(project_id: str, raw: str) -> str:
    """Map a bare filename to the project's stored absolute file_path.

    Layered fallback for callers that hand us a filename instead of a path
    (the LLM typically does this -- it knows the original document name from
    the user, not the disk location). Returns the input unchanged if no
    matching document is found or the project store isn't reachable.
    """
    if not raw or not project_id or not isinstance(raw, str):
        return raw
    try:
        if os.path.isabs(raw) and os.path.exists(raw):
            return raw
    except (TypeError, ValueError):
        return raw
    try:
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
    except Exception:
        return raw

    needle = os.path.basename(str(raw)).strip().lower()
    if not needle:
        return raw

    # Pass 1: exact (case-insensitive) match.
    for doc in docs:
        on = (doc.get("original_name") or "").strip().lower()
        if on and on == needle:
            fp = doc.get("file_path") or ""
            if fp and os.path.exists(fp):
                return fp

    # Pass 2: substring match either direction (LLM truncations / rewordings).
    for doc in docs:
        on = (doc.get("original_name") or "").strip().lower()
        if on and (needle in on or on in needle):
            fp = doc.get("file_path") or ""
            if fp and os.path.exists(fp):
                return fp

    return raw


class BOQProcessorBlock(UniversalBlock):
    name = "boq_processor"
    version = "1.1.0"
    description = "Parse Excel/CSV/PDF Bills of Quantities into structured quantities and cost breakdown"
    layer = 3
    tags = ["domain", "construction", "boq", "quantities", "excel", "pdf"]
    requires = []

    default_config = {
        "currency": "USD",
        "include_zero_qty": False,
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".xlsx", ".xls", ".csv", ".pdf"],
            "placeholder": "Upload BOQ spreadsheet (.xlsx, .csv) or BOQ PDF...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "item_count", "type": "number", "label": "Line Items"},
                {"name": "total_cost", "type": "number", "unit": "USD", "label": "Total Cost"},
                {"name": "line_items", "type": "list", "label": "Line Items"},
                {"name": "cost_breakdown", "type": "json", "label": "Cost Breakdown"},
            ],
        },
        "quick_actions": [
            {"icon": "", "label": "Parse BOQ", "prompt": "Parse and summarize this Bill of Quantities"},
            {"icon": "", "label": "Cost Summary", "prompt": "Give me a cost breakdown by trade/division"},
        ],
    }

    # Common BOQ column name aliases
    _COL_MAP = {
        "description": ["description", "item_description", "work_item", "item", "activity", "desc", "name"],
        "quantity": ["quantity", "qty", "no", "number", "count"],
        "unit": ["unit", "uom", "u/m", "unit_of_measure", "measure"],
        "rate": ["rate", "unit_cost", "unit_price", "price", "unit_rate", "cost_per_unit", "cost/unit"],
        "total": ["total", "total_cost", "amount", "line_total", "extended_price", "cost", "value"],
        "section": ["section", "division", "trade", "category", "csi_div", "package", "work_package"],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        file_path = data.get("file_path") or params.get("file_path") or data.get("text") or data.get("input") or (input_data if isinstance(input_data, str) else "")
        if not file_path:
            return {"status": "error", "error": "No file_path provided. Requires an .xlsx, .csv, or .pdf BOQ file path."}

        # LLM callers typically pass a bare filename ("Demolition BOQ.pdf") rather
        # than the stored absolute path. Try to resolve that against the project's
        # uploaded documents before failing with "File not found".
        if not os.path.exists(str(file_path)):
            project_id = (
                params.get("project_id")
                or (data.get("project_id") if isinstance(data, dict) else None)
            )
            if project_id:
                resolved = _resolve_via_project(str(project_id), str(file_path))
                if resolved and resolved != file_path and os.path.exists(resolved):
                    file_path = resolved

        if not os.path.exists(str(file_path)):
            return {"status": "error", "error": f"File not found: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        try:
            # open_plaintext transparently decrypts when DATA_ENCRYPTION_KEY is
            # set on the server (uploads go through file_crypto.write_document)
            # and is a no-op for legacy plaintext files.
            from app.core.file_crypto import open_plaintext
            with open_plaintext(file_path) as plain_path:
                if ext == ".csv":
                    return await self._parse_csv(plain_path, params)
                elif ext in (".xlsx", ".xls"):
                    return await self._parse_excel(plain_path, params)
                elif ext == ".pdf":
                    return await self._parse_pdf(plain_path, params)
                else:
                    return {
                        "status": "error",
                        "error": f"Unsupported format: {ext}. Use .xlsx, .csv, or .pdf",
                    }
        except ImportError as e:
            return {
                "status": "error",
                "error": f"Missing dependency: {e}. Run: pip install pandas openpyxl pdfplumber",
            }
        except Exception as e:
            return {"status": "error", "error": f"Parse error: {e}"}

    async def _parse_csv(self, file_path: str, params: Dict) -> Dict:
        import pandas as pd
        df = pd.read_csv(file_path)
        return self._process_dataframe(df, params)

    async def _parse_excel(self, file_path: str, params: Dict) -> Dict:
        import pandas as pd
        sheet = params.get("sheet_name", 0)
        df = pd.read_excel(file_path, sheet_name=sheet, engine="openpyxl")
        return self._process_dataframe(df, params)

    async def _parse_pdf(self, file_path: str, params: Dict) -> Dict:
        """Extract tabular BOQ data from a PDF via pdfplumber.

        Walks every page, calls ``page.extract_tables()``, treats row 0 of each
        table as a header, then groups tables by header signature so
        continuation pages with identical headers concatenate into a single
        DataFrame. The largest such group is treated as the BOQ and dispatched
        to ``_process_dataframe`` (the same path Excel/CSV use), so the column
        aliasing in ``_resolve_columns`` works for any BOQ table format.

        Returns the standard BOQ result (item_count, total_cost, line_items,
        cost_breakdown). If no extractable tables are found (e.g. a pure-image
        scanned PDF that wasn't OCR'd first), surfaces a clear error pointing
        to that limitation.
        """
        import pdfplumber
        import pandas as pd

        # Group tables by a canonical "shape" key built from normalized headers
        # so continuation pages merge naturally.
        groups: Dict[Tuple[str, ...], List[List[List[str]]]] = {}
        primary_headers_for: Dict[Tuple[str, ...], List[str]] = {}
        page_table_count = 0
        # Track pages we silently dropped so the caller knows the BOQ totals
        # may be incomplete. Previously these failures were swallowed, which
        # turned a partial-parse into a wrong client deliverable.
        pages_skipped = 0
        pages_skipped_reasons: List[Dict[str, Any]] = []

        with pdfplumber.open(file_path) as pdf:
            for page_index, page in enumerate(pdf.pages, 1):
                try:
                    tables = page.extract_tables() or []
                except Exception as exc:
                    _logger.warning(
                        "boq_processor: page %s extract_tables failed: %s",
                        page_index,
                        exc,
                    )
                    pages_skipped += 1
                    pages_skipped_reasons.append({
                        "page": page_index,
                        "stage": "extract_tables",
                        "error": str(exc),
                    })
                    continue
                for tbl in tables:
                    if not tbl or len(tbl) < 2:
                        continue
                    headers_raw = [(str(c or "").strip()) for c in tbl[0]]
                    if not any(headers_raw):
                        continue
                    n_cols = len(headers_raw)
                    if n_cols < 2:
                        continue
                    # Canonical shape key: tuple of normalized header tokens so
                    # tables across pages with identical structure merge.
                    key = tuple(self._normalize_col(h) for h in headers_raw)
                    norm_rows: List[List[str]] = []
                    for row in tbl[1:]:
                        if row is None:
                            continue
                        cells = [(str(c) if c is not None else "") for c in row]
                        if len(cells) < n_cols:
                            cells = cells + [""] * (n_cols - len(cells))
                        elif len(cells) > n_cols:
                            cells = cells[:n_cols]
                        if not any(s.strip() for s in cells):
                            continue  # blank row
                        norm_rows.append(cells)
                    if not norm_rows:
                        continue
                    groups.setdefault(key, []).extend(norm_rows)
                    primary_headers_for.setdefault(key, headers_raw)
                    page_table_count += 1

        if not groups:
            # pdfplumber found no tabular structure. This is normal for scanned
            # BOQ PDFs where OCR captured the text but page layout doesn't yield
            # a clean table grid. Return per-page text so the caller (typically
            # an LLM agent) can extract line items from the raw OCR text.
            page_texts: List[Dict[str, Any]] = []
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    try:
                        t = (page.extract_text() or "").strip()
                    except Exception as exc:
                        _logger.warning(
                            "boq_processor: page %s extract_text failed: %s",
                            i,
                            exc,
                        )
                        pages_skipped += 1
                        pages_skipped_reasons.append({
                            "page": i,
                            "stage": "extract_text",
                            "error": str(exc),
                        })
                        t = ""
                    if t:
                        page_texts.append({"page": i, "text": t})
            if page_texts:
                return {
                    "status": "partial",
                    "source_format": "pdf",
                    "note": (
                        "PDF has no clean tabular structure (likely scanned). "
                        "Returning raw page text — pass to an LLM for BOQ line-item extraction."
                    ),
                    "page_count": len(page_texts),
                    "page_texts": page_texts,
                    "pages_skipped": pages_skipped,
                    "pages_skipped_reasons": pages_skipped_reasons,
                }
            return {
                "status": "error",
                "error": (
                    "No extractable tables or text found in this PDF. The file "
                    "may be a pure-image scan that hasn't been OCR'd. Run OCR "
                    "first or re-upload as .xlsx/.csv."
                ),
                "pages_skipped": pages_skipped,
                "pages_skipped_reasons": pages_skipped_reasons,
            }

        # Pick the largest group as the BOQ; other groups (smaller summary
        # tables, headers/footers picked up as tables) are ignored.
        best_key = max(groups.keys(), key=lambda k: len(groups[k]))
        df = pd.DataFrame(groups[best_key], columns=primary_headers_for[best_key])

        result = self._process_dataframe(df, params)
        # Surface PDF-specific diagnostics so the caller knows we used the PDF path.
        if isinstance(result, dict):
            result.setdefault("source_format", "pdf")
            result["pdf_tables_total"] = page_table_count
            result["pdf_tables_used"] = len(groups[best_key])
            # Surface dropped pages so callers can flag the BOQ total as
            # potentially incomplete instead of silently shipping a low number.
            result["pages_skipped"] = pages_skipped
            result["pages_skipped_reasons"] = pages_skipped_reasons
        return result

    @staticmethod
    def _normalize_col(name: str) -> str:
        """Reduce a raw column header to a canonical token for alias matching.

        Strips parenthesized suffixes (currency / unit hints) and a small set of
        trailing currency tokens, then lowercases and replaces spaces/slashes
        with underscores. Lets us match real BOQ headers like 'Rate (SAR)',
        'Amount (USD)', 'Qty.', 'Item No.' against the short alias list.
        """
        import re
        n = name.strip()
        # Strip any (...) suffix — usually a currency or unit qualifier.
        n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
        # Strip trailing currency tokens with optional punctuation.
        n = re.sub(
            r"[\s,;:]+(SAR|USD|AED|EUR|GBP|JPY|CNY|AUD|CAD|KWD|QAR|BHD|OMR)\b\.?$",
            "",
            n,
            flags=re.IGNORECASE,
        )
        # Drop trailing punctuation ("Qty.", "Item No.").
        n = n.rstrip(" .,:;")
        n = n.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        # Collapse repeats of underscore so 'item__no' becomes 'item_no'.
        n = re.sub(r"_+", "_", n).strip("_")
        return n

    def _resolve_columns(self, columns: List[str]) -> Dict[str, str]:
        """Map alias-set field names to actual DataFrame column names.

        Two-pass: exact normalized match first (cheap, deterministic), then
        substring match against the same alias set as a fallback so columns
        like 'unit_rate_in_sar' still resolve when the prefix is recognised.
        """
        resolved: Dict[str, str] = {}
        normalized = [self._normalize_col(c) for c in columns]
        for field, candidates in self._COL_MAP.items():
            chosen_idx = None
            # Pass 1: exact normalized match.
            for c in candidates:
                if c in normalized:
                    chosen_idx = normalized.index(c)
                    break
            # Pass 2: substring match (longest alias first so "unit_rate" beats "unit").
            if chosen_idx is None:
                for c in sorted(candidates, key=len, reverse=True):
                    for i, ncol in enumerate(normalized):
                        if c in ncol.split("_") or ncol.startswith(c + "_") or ncol.endswith("_" + c):
                            chosen_idx = i
                            break
                    if chosen_idx is not None:
                        break
            if chosen_idx is not None:
                resolved[field] = columns[chosen_idx]
        return resolved

    def _process_dataframe(self, df, params: Dict) -> Dict:
        df.columns = [str(c).strip() for c in df.columns]
        resolved = self._resolve_columns(list(df.columns))

        include_zero = params.get("include_zero_qty", self.config.get("include_zero_qty", False))
        currency = params.get("currency", self.config.get("currency", "USD"))

        line_items: List[Dict] = []
        section_totals: Dict[str, float] = {}
        warnings: List[str] = []
        skipped_items: List[Dict] = []

        quantity_col_resolved = "quantity" in resolved
        if not quantity_col_resolved:
            warnings.append(
                "No quantity column detected; zero-quantity filter disabled and all rows retained."
            )

        for _, row in df.iterrows():
            description = str(row.get(resolved.get("description", ""), "")).strip()
            if not description or description.lower() == "nan":
                continue

            raw_qty = row.get(resolved.get("quantity", ""), 0)
            try:
                qty = _to_float(raw_qty)
            except ValueError:
                skipped_items.append({"description": description, "raw_value": raw_qty})
                continue
            if quantity_col_resolved and not include_zero and qty == 0:
                continue

            rate = _to_float_safe(row.get(resolved.get("rate", ""), 0))
            total = _to_float_safe(row.get(resolved.get("total", ""), 0))
            if total == 0 and qty > 0 and rate > 0:
                total = qty * rate

            unit = str(row.get(resolved.get("unit", ""), "")).strip()
            section = str(row.get(resolved.get("section", ""), "General")).strip()
            if section.lower() == "nan":
                section = "General"

            item_key = description.lower().replace(" ", "_")[:50]

            line_items.append(
                {
                    "item_key": item_key,
                    "description": description,
                    "quantity": qty,
                    "unit": unit if unit != "nan" else "",
                    "unit_cost": rate,
                    "total_cost": round(total, 2),
                    "section": section,
                    "currency": currency,
                }
            )
            section_totals[section] = section_totals.get(section, 0.0) + total

        total_cost = sum(i["total_cost"] for i in line_items)
        cost_breakdown = {
            section: {
                "total": round(v, 2),
                "percentage": round(v / total_cost * 100, 1) if total_cost > 0 else 0,
            }
            for section, v in sorted(section_totals.items(), key=lambda x: x[1], reverse=True)
        }

        result = {
            "status": "success",
            "item_count": len(line_items),
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "line_items": line_items,
            "cost_breakdown": cost_breakdown,
            "sections": list(section_totals.keys()),
            "columns_detected": resolved,
        }
        if warnings:
            result["warnings"] = warnings
        if skipped_items:
            result["skipped_items"] = skipped_items
        return result


def _to_float(val) -> float:
    """Coerce ``val`` to float. Raises ``ValueError`` for non-empty non-numeric
    strings so callers can distinguish a true zero from an unparseable value
    like ``"Lot"`` or ``"Provisional Sum"``."""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").strip()
    if s == "" or s.lower() == "nan":
        return 0.0
    try:
        return float(s)
    except TypeError:
        return 0.0


def _to_float_safe(val) -> float:
    """Best-effort coercion to float; returns 0.0 on any failure."""
    try:
        return _to_float(val)
    except (ValueError, TypeError):
        return 0.0
