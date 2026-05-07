"""Spec Analyzer Block - Extract grade requirements, material specs, compliance flags from PDFs"""

import os
import re
from typing import Any, Dict, List, Tuple
from app.core.universal_base import UniversalBlock


class SpecAnalyzerBlock(UniversalBlock):
    name = "spec_analyzer"
    version = "1.0.0"
    description = "Extract grade requirements, material specs, and compliance flags from specification PDFs"
    layer = 3
    tags = ["domain", "construction", "specs", "pdf", "compliance", "materials"]
    requires = ["pdf"]

    default_config = {
        "max_pages": 100,
        "compliance_standards": ["astm", "aci", "aisc", "iso", "bs", "en", "saso"],
    }

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".pdf"],
            "placeholder": "Upload project specification PDF...",
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "grade_requirements", "type": "list", "label": "Grade Requirements"},
                {"name": "material_specs", "type": "list", "label": "Material Specs"},
                {"name": "compliance_flags", "type": "list", "label": "Compliance Flags"},
                {"name": "sections_found", "type": "number", "label": "Sections"},
            ],
        },
        "quick_actions": [
            {"icon": "📋", "label": "Extract Specs", "prompt": "Extract all material specifications and grade requirements"},
            {"icon": "✅", "label": "Check Compliance", "prompt": "Check specification for ASTM/ACI/SASO compliance requirements"},
            {"icon": "🔍", "label": "Find Standards", "prompt": "List all referenced standards and codes"},
        ],
    }

    _GRADE_PATTERNS: List[Tuple[str, str]] = [
        (r"\b(?:grade|class|type)\s*[:\-]?\s*([A-Z0-9\-]+)", "grade"),
        (r"\bfc['\"]?\s*=\s*([\d,]+)\s*(?:psi|MPa|kPa)", "concrete_strength"),
        (r"\bfy\s*=\s*([\d,]+)\s*(?:psi|MPa)", "rebar_yield"),
        (r"\bASTM\s+([A-Z]\d+(?:\/[A-Z]\d+)?)", "astm_standard"),
        (r"\bACI\s+(\d+\w*)", "aci_standard"),
        (r"\bAISC\s+(\d+\w*)", "aisc_standard"),
        (r"\bISO\s+(\d+(?:[-:]\d+)?)", "iso_standard"),
        (r"\bBS\s+(\d+(?:[-:]\d+)?)", "bs_standard"),
        (r"\bEN\s+(\d+(?:[-:]\d+)?)", "en_standard"),
        (r"\bSASO\s+(\d+(?:[-:]\d+)?)", "saso_standard"),
        (r"\b(\d+)\s*MPa\s*(?:concrete|compressive)", "concrete_strength_mpa"),
        (r"\b(\d+)\s*N/mm[²2]", "strength_nmm2"),
    ]

    _MATERIAL_PATTERNS: List[Tuple[str, str]] = [
        (r"(?:reinforcing|reinforcement)\s+(?:steel|bars?)\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "rebar"),
        (r"concrete\s+(?:shall\s+be\s+)?(?:of\s+)?(?:class|grade|strength)?\s*([^\.\n]{5,80})", "concrete"),
        (r"structural\s+steel\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "structural_steel"),
        (r"(?:cement|portland\s+cement)\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "cement"),
        (r"aggregate\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "aggregate"),
        (r"waterproof(?:ing)?\s+(?:membrane|material|system)?\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "waterproofing"),
        (r"insulation\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "insulation"),
        (r"paint\s+(?:system|coat|finish)?\s+(?:shall\s+be\s+)?([^\.\n]{5,80})", "paint"),
    ]

    _COMPLIANCE_KEYWORDS: List[Tuple[str, str]] = [
        ("shall conform to", "conformance"),
        ("shall comply with", "compliance"),
        ("shall be in accordance with", "accordance"),
        ("minimum requirement", "minimum_requirement"),
        ("not less than", "minimum_value"),
        ("not exceed", "maximum_value"),
        ("must be approved", "approval_required"),
        ("submittal required", "submittal"),
        ("shop drawing", "shop_drawing"),
        ("test certificate", "test_certificate"),
        ("material approval", "material_approval"),
        ("mock-up", "mockup_required"),
    ]

    _SECTION_PATTERNS = [
        r"SECTION\s+\d+\s*[-–]\s*([A-Z][A-Z\s,/]{3,60})",
        r"^\s*(\d{2}\s+\d{2}\s+\d{2})\s+([A-Z][A-Z\s,/]{3,50})",
        r"(?:PART|ARTICLE)\s+\d+[:\s]+([A-Z][A-Z\s,/]{3,60})",
    ]

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        file_path = data.get("file_path") or params.get("file_path")
        if not file_path:
            return {"status": "error", "error": "No file_path provided"}

        if not os.path.exists(file_path):
            return {"status": "error", "error": f"File not found: {file_path}"}

        try:
            text, page_count = self._extract_text(file_path, params)
        except ImportError:
            return {
                "status": "error",
                "error": "pymupdf not installed. Run: pip install pymupdf",
            }
        except Exception as e:
            return {"status": "error", "error": f"PDF extraction failed: {e}"}

        grade_requirements = self._extract_grades(text)
        material_specs = self._extract_materials(text)
        compliance_flags = self._extract_compliance(text)
        sections = self._extract_sections(text)

        referenced_standards = list(
            {g["type"] for g in grade_requirements if "standard" in g["type"]}
        )

        return {
            "status": "success",
            "grade_requirements": grade_requirements,
            "material_specs": material_specs,
            "compliance_flags": compliance_flags,
            "sections_found": len(sections),
            "sections": sections[:20],
            "page_count": page_count,
            "standards_referenced": referenced_standards,
        }

    def _extract_text(self, file_path: str, params: Dict) -> Tuple[str, int]:
        import fitz
        max_pages = int(params.get("max_pages", self.config.get("max_pages", 100)))
        doc = fitz.open(file_path)
        pages = min(len(doc), max_pages)
        text = "\n".join(doc[i].get_text() for i in range(pages))
        doc.close()
        return text, pages

    def _extract_grades(self, text: str) -> List[Dict]:
        found: Dict[str, Dict] = {}
        for pattern, ptype in self._GRADE_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                val = m.group(1).strip()
                key = f"{ptype}:{val}"
                if key not in found:
                    ctx_start = max(0, m.start() - 80)
                    ctx_end = min(len(text), m.end() + 80)
                    found[key] = {
                        "type": ptype,
                        "value": val,
                        "context": text[ctx_start:ctx_end].replace("\n", " ").strip(),
                    }
        return list(found.values())

    def _extract_materials(self, text: str) -> List[Dict]:
        found: Dict[str, Dict] = {}
        for pattern, mtype in self._MATERIAL_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                val = m.group(1).strip()
                if len(val) < 5:
                    continue
                key = f"{mtype}:{val[:40]}"
                if key not in found:
                    found[key] = {
                        "material_type": mtype,
                        "specification": val[:200],
                        "full_match": m.group(0)[:200],
                    }
        return list(found.values())

    def _extract_compliance(self, text: str) -> List[Dict]:
        flags: List[Dict] = []
        lower = text.lower()
        for keyword, flag_type in self._COMPLIANCE_KEYWORDS:
            idx = 0
            count = 0
            while count < 3:
                pos = lower.find(keyword, idx)
                if pos == -1:
                    break
                ctx_start = max(0, pos - 40)
                ctx_end = min(len(text), pos + len(keyword) + 120)
                flags.append(
                    {
                        "flag_type": flag_type,
                        "keyword": keyword,
                        "context": text[ctx_start:ctx_end].replace("\n", " ").strip(),
                    }
                )
                count += 1
                idx = pos + len(keyword)
        return flags

    def _extract_sections(self, text: str) -> List[str]:
        sections: List[str] = []
        seen: set = set()
        for pattern in self._SECTION_PATTERNS:
            for m in re.finditer(pattern, text, re.MULTILINE):
                if m.lastindex and m.lastindex >= 2:
                    title = f"{m.group(1)} {m.group(2)}".strip()
                else:
                    title = m.group(1).strip()
                if title not in seen and len(title) > 3:
                    seen.add(title)
                    sections.append(title)
        return sections
