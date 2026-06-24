"""Construction container — documents submodule."""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.construction_types import Measurement, SpecItem, RiskItem

from .helpers import _parse_money_str, _safe_float, _safe_iso_date

logger = logging.getLogger(__name__)


class ConstructionDocumentsMixin:
    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".pdf", ".ifc", ".dwg", ".jpg", ".png", ".xer", ".xml"],
            "placeholder": "Upload construction drawing, BIM model, schedule, or contract...",
            "multiline": True
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "concrete_volume_m3", "type": "number", "unit": "m³", "label": "Concrete"},
                {"name": "steel_weight_kg", "type": "number", "unit": "kg", "label": "Steel"},
                {"name": "floor_area_m2", "type": "number", "unit": "m²", "label": "Floor Area"},
                {"name": "rebar_length_m", "type": "number", "unit": "m", "label": "Rebar"},
                {"name": "confidence", "type": "percentage", "label": "Confidence"}
            ]
        },
        "quick_actions": [
            {"icon": "📐", "label": "Measure Drawing", "prompt": "Extract all measurements from this drawing"},
            {"icon": "📊", "label": "Calculate Quantities", "prompt": "Calculate BOQ from this drawing"},
            {"icon": "⚠️", "label": "Check Compliance", "prompt": "Check this against Saudi building codes"},
            {"icon": "🌱", "label": "Carbon Estimate", "prompt": "Estimate embodied carbon for this project"},
            {"icon": "📅", "label": "Analyze Schedule", "prompt": "Analyze this Primavera schedule for risks"}
        ]
    }
    default_config = {
        "confidence_threshold": 0.85,
        "default_trade": "concrete"
    }
    requires = [
        "pdf", "ocr", "image",
        # Week 1
        "boq_processor", "spec_analyzer", "sympy_reasoning",
        # Week 2
        "drawing_qto", "primavera_parser", "smart_orchestrator",
        # Week 3
        "formula_executor_v2", "bim_extractor",
        # Week 4
        "learning_engine", "recommendation_template",
        # historical_benchmark removed — learning_engine accumulates real data
    ]
    tags = ["domain", "container", "aec", "construction", "bim"]
    layer = 3
    description = "Complete AEC suite: BIM, QA/QC, scheduling, contracts, specs, safety, carbon, procurement, risk"
    version = "3.1"
    name = "construction"
    """
    Construction Container: Complete AEC suite - BIM, QA/QC, scheduling,
    contracts, specs, safety, carbon, procurement, risk
    """
    async def process_document(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        file_path = data.get("file_path") or p.get("file_path")
        url = data.get("url") or p.get("url")
        doc_type = p.get("doc_type", "auto")

        if not file_path and url:
            file_path = await self._download_file(url)

        if not file_path:
            return {"status": "error", "error": "No file provided"}

        if doc_type == "auto":
            doc_type = await self._classify_document(file_path)

        cache_key = await self._get_or_create_cache_key(file_path, doc_type)

        from app.blocks import BLOCK_REGISTRY
        cache_block = BLOCK_REGISTRY.get("cache_manager")
        if cache_block:
            try:
                cache_instance = cache_block()
                cached = await cache_instance.execute(
                    {"key": cache_key}, {"action": "get", "key": cache_key}
                )
                if cached.get("cached") and cached.get("value") is not None:
                    cached_value = cached["value"]
                    if isinstance(cached_value, dict):
                        cached_value["_source"] = "cache"
                        cached_value["_cache_key"] = cache_key
                    return cached_value
            except Exception:
                pass

        file_size = 0
        hasher_block = BLOCK_REGISTRY.get("file_hasher")
        if hasher_block:
            try:
                hasher_instance = hasher_block()
                hash_result = await hasher_instance.execute(
                    {"file_path": file_path}, {"action": "metadata"}
                )
                if hash_result.get("status") == "success":
                    file_size = hash_result.get("size", 0)
            except Exception:
                pass

        if file_size > 10 * 1024 * 1024:
            async_block = BLOCK_REGISTRY.get("async_processor")
            if async_block:
                try:
                    async_instance = async_block()
                    task_payload = {
                        "task_name": "block:construction.process_document",
                        "file_path": file_path,
                        "doc_type": doc_type,
                        "data": data,
                        "params": p,
                    }
                    queued = await async_instance.execute(
                        task_payload,
                        {
                            "action": "submit",
                            "task_name": "block:construction.process_document",
                        },
                    )
                    return {
                        "status": "queued",
                        "_source": "async_queue",
                        "_cache_key": cache_key,
                        "file_size": file_size,
                        "queued": queued,
                    }
                except Exception:
                    pass

        processors = {
            "drawing": self._process_drawing,
            "specification": self.process_specification_full,
            "contract": self.process_contract,
            "schedule": self.parse_primavera_schedule,
            "bom": self._process_bill_of_materials,
            "report": self._process_report,
            "bim": self._process_ifc,
            "image": self._process_site_photo,
            "change_order": self.change_order_impact,
            "safety_audit": self.safety_compliance_audit,
        }

        processor = processors.get(doc_type, self._process_drawing)
        p["file_path"] = file_path
        result = await processor(file_path, p)

        llm_block = BLOCK_REGISTRY.get("llm_enhancer")
        if llm_block and isinstance(result, dict) and result.get("status") == "success":
            try:
                llm_instance = llm_block()
                enhanced = await llm_instance.execute(
                    {"text": json.dumps(result)},
                    {
                        "action": "structure_json",
                        "schema": "structured construction document data",
                    },
                )
                if enhanced.get("status") == "success":
                    result["llm_enhanced"] = enhanced.get("structured") or enhanced
            except Exception:
                pass

        if cache_block:
            try:
                cache_instance = cache_block()
                await cache_instance.execute(
                    result, {"action": "set", "key": cache_key, "ttl": 7200}
                )
            except Exception:
                pass

        if isinstance(result, dict):
            result["_cache_key"] = cache_key
            result["_source"] = "processor"
        return result
    async def _process_drawing(self, file_path: str, params: Dict) -> Dict:
        # Use pre-extracted text if provided from chain
        pre_extracted_text = params.get("extracted_text", "")
    
        try:
            import fitz
            doc = fitz.open(file_path)
        except Exception as e:
            return {"status": "error", "error": f"[DRAWING_V2] Could not open file: {str(e)}", "file": file_path}
    
        result = {
            "status": "success",
            "doc_type": "drawing",
            "file_name": Path(file_path).name,
            "drawing_number": self._extract_drawing_number(Path(file_path).name),
            "revision": self._extract_revision(Path(file_path).name),
            "total_pages": len(doc),
            "sheets": [],
            "measurements": [],
            "tables": [],
            "annotations": [],
            "specifications": [],
            "detected_disciplines": [],
            "scale": None,
            "title_block": {},
            "bom_items": [],
            "confidence": {},
            "used_pre_extracted_text": bool(pre_extracted_text)  # Flag to indicate source
        }
    
        for page_num in range(len(doc)):
            page = doc[page_num]
            sheet_data = self._process_drawing_page(page, page_num, pre_extracted_text if page_num == 0 else "")
            result["sheets"].append(sheet_data)
            result["measurements"].extend(sheet_data["measurements"])
            result["tables"].extend(sheet_data["tables"])
            result["annotations"].extend(sheet_data["annotations"])
            result["specifications"].extend(sheet_data["specs"])
            result["detected_disciplines"].extend(self._detect_disciplines(sheet_data["raw_text"]))
    
        if result["sheets"]:
            result["title_block"] = self._extract_title_block(result["sheets"][0])
            result["scale"] = self._extract_scale(result["sheets"][0]["raw_text"])
    
        result["quantities"] = self._calculate_quantities(result["measurements"])
        result["cost_estimate"] = self._estimate_costs(result["quantities"])
        result["carbon_estimate"] = self._estimate_carbon(result["quantities"])
        result["confidence"] = self._calculate_confidence(result)
        result["auto_risks"] = await self._detect_risks_from_drawing(result)
    
        doc.close()
        return result
    async def process_contract(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        file_path = data.get("file_path") or p.get("file_path")
        contract_type = p.get("contract_type", "general")
    
        if not file_path:
            return {"status": "error", "error": "No contract file provided"}
    
        try:
            import fitz
            doc = fitz.open(file_path)
            full_text = ""
            for page in doc:
                full_text += page.get_text()
            doc.close()
        except Exception as e:
            return {"status": "error", "error": f"Could not read contract: {str(e)}"}
    
        clause_patterns = {
            "payment_terms": r'(?:payment|pay|invoice)[\s\w]{0,50}(?:term|schedule|milestone|certificate)',
            "liquidated_damages": r'(?:liquidated damages|ld|delay damages)[\s\w]{0,100}(?:rate|amount|per day)',
            "retention": r'(?:retention|retainage)[\s\w]{0,50}(?:percent|percentage|amount|release)',
            "insurance": r'(?:insurance|indemnif)[\s\w]{0,100}(?:required|shall|must|coverage)',
            "termination": r'(?:terminat|cancel|end)[\s\w]{0,100}(?:notice|for cause|convenience)',
            "force_majeure": r'(?:force majeure|unforeseen|beyond control|delay event)[\s\w]{0,100}(?:excus|reliev|not liable)',
            "dispute_resolution": r'(?:dispute|arbitration|mediation|adjudication)[\s\w]{0,100}(?:shall|must|proceed)',
        }
    
        extracted_clauses = {}
        for clause_name, pattern in clause_patterns.items():
            matches = list(re.finditer(pattern, full_text, re.IGNORECASE))
            extracted_clauses[clause_name] = {
                "found": len(matches) > 0,
                "count": len(matches),
                "examples": [m.group(0)[:200] for m in matches[:3]]
            }
    
        obligations = self._extract_obligations(full_text)
        contract_risks = self._assess_contract_risks(extracted_clauses, contract_type)
        financial_terms = self._extract_financial_terms(full_text)
    
        return {
            "status": "success",
            "action": "contract_analysis",
            "file_name": Path(file_path).name,
            "contract_type": contract_type,
            "document_length": len(full_text),
            "clauses_found": len([c for c in extracted_clauses.values() if c.get("found")]),
            "total_clauses": len(clause_patterns),
            "extracted_clauses": extracted_clauses,
            "key_obligations": obligations,
            "financial_terms": financial_terms,
            "risk_assessment": {
                "overall_score": contract_risks["score"],
                "risk_level": contract_risks["level"],
                "critical_issues": contract_risks["critical"],
                "warnings": contract_risks["warnings"],
                "recommendations": contract_risks["recommendations"]
            },
            "summary": self._generate_contract_summary(extracted_clauses, financial_terms)
        }
    @staticmethod
    def _split_csi_divisions(full_text: str, division_filter=None) -> tuple:
        """CSI MasterFormat division-splitting (container-only — the block has no equivalent).

        Groups raw spec text into Divisions 01–49 by leading 2-digit codes.
        Returns (detected_divisions, division_spec_items).
        """
        divisions = {i: [] for i in range(1, 50)}
        current_division = None
        for line in full_text.split('\n'):
            # \s{2,} is intentional and unified across PDF and extracted-text
            # inputs: the old file-path-only path used \s{3,}, but \s{2,} is the
            # more permissive of the two and matches everything \s{3,} would.
            m = re.match(r'^(\d{2})\s{2,}', line)
            if m:
                div_num = int(m.group(1))
                if 1 <= div_num <= 49:
                    current_division = div_num
                    divisions[current_division].append(line.strip())
            elif current_division and line.strip():
                divisions[current_division].append(line.strip())

        detected = [i for i, c in divisions.items() if c]
        division_items = []
        for div_num, content in divisions.items():
            if not content:
                continue
            if division_filter and str(div_num) != str(division_filter):
                continue
            division_items.append({
                "category": f"Division {div_num:02d}",
                "key": "content",
                "value": f"{len(content)} paragraphs",
                "section": "general",
                "confidence": 0.9,
            })
        return detected, division_items
    async def process_specification_full(self, input_data: Any, params: Dict) -> Dict:
        """Analyse a project specification.

        Delegates genuine grade / material / compliance extraction to the
        spec_analyzer block — no demo mode, no fabricated divisions. The CSI
        MasterFormat division-splitting layer (which the block has no equivalent
        for) stays here in the container.
        """
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        file_path = data.get("file_path") or p.get("file_path")
        extracted_text = data.get("extracted_text") or p.get("extracted_text") or ""
        division_filter = p.get("division")

        if not file_path and not extracted_text:
            return {
                "status": "error",
                "action": "specification_analysis",
                "error": "No specification provided — pass file_path (PDF) or extracted_text",
            }

        block = self._get_spec_analyzer_block()
        if block is None:
            return {
                "status": "error",
                "action": "specification_analysis",
                "error": "spec_analyzer block unavailable — cannot extract grades/materials/compliance",
            }

        # Delegate grade/material/compliance extraction to the block.
        block_input = {"file_path": file_path} if file_path else {"text": extracted_text}
        result = await block.process(block_input, p)
        if not isinstance(result, dict) or result.get("status") != "success":
            err = result.get("error") if isinstance(result, dict) else "spec_analyzer block failed"
            return {
                "status": "error",
                "action": "specification_analysis",
                "error": err or "spec_analyzer block failed",
            }

        grade_requirements = result.get("grade_requirements", []) or []
        material_specs = result.get("material_specs", []) or []
        compliance_flags = result.get("compliance_flags", []) or []

        # CSI division-splitting — container-only layer, the block has no equivalent.
        # The block already extracted PDF text; re-read it here only for splitting.
        full_text = extracted_text
        if file_path:
            try:
                import fitz
                doc = fitz.open(file_path)
                # Join with newline so the last line of page N and the first
                # line of page N+1 stay distinct; previously a bare "".join
                # silently merged them and broke `_split_csi_divisions`'s
                # line-anchored regex on multi-page spec PDFs.
                full_text = "\n".join(page.get_text() for page in doc)
                doc.close()
            except Exception as e:
                return {"status": "error", "action": "specification_analysis",
                        "error": f"Could not read spec file for division-splitting: {str(e)}"}

        detected_divisions, division_items = self._split_csi_divisions(full_text, division_filter)

        # Map the block's output into the spec_items shape callers expect:
        # one item per CSI division, plus one item per extracted grade / material.
        spec_items = list(division_items)
        for g in grade_requirements:
            spec_items.append({
                "category": "Grade Requirement",
                "key": g.get("type", "grade"),
                "value": g.get("value", ""),
                "section": g.get("context", ""),
                "confidence": 0.9,
            })
        for m in material_specs:
            spec_items.append({
                "category": "Material Spec",
                "key": m.get("material_type", "material"),
                "value": m.get("specification", ""),
                "section": "materials",
                "confidence": 0.85,
            })

        # Derive testing / QA-QC response keys from the block's compliance flags
        # (the block's compliance_flags supersede the old binary sentinel helpers).
        testing_flags = {"test_certificate"}
        qaqc_flags = {"shop_drawing", "mockup_required", "submittal", "material_approval", "approval_required"}
        testing_requirements = [
            f.get("context", f.get("keyword", "")) for f in compliance_flags
            if f.get("flag_type") in testing_flags
        ]
        qa_qc_requirements = [
            f.get("context", f.get("keyword", "")) for f in compliance_flags
            if f.get("flag_type") in qaqc_flags
        ]

        # Top-up pass over full_text — the block's compliance keyword/pattern sets
        # are narrower than the old _extract_testing_requirements / _extract_qaqc
        # helpers, which fired on bare words. Restore that coverage and UNION it
        # with the block's richer flags (deduplicated, order-preserving).
        testing_requirements = self._topup_keyword_matches(
            full_text, ["test", "sample", "lab"], existing=testing_requirements,
        )
        qa_qc_requirements = self._topup_keyword_matches(
            full_text, ["inspection", "witness", "hold point", "hold-point"],
            existing=qa_qc_requirements,
        )

        # materials_referenced: UNION the block-derived material types with a
        # substring pass over the old _extract_materials 10-keyword set, since the
        # block's material_specs drop brick/block/glass/aluminum/timber. Deduplicated.
        material_keywords = [
            "concrete", "steel", "rebar", "brick", "block", "glass",
            "aluminum", "timber", "insulation", "membrane",
        ]
        materials_seen = set()
        materials_referenced = []
        for m in material_specs:
            mt = m.get("material_type", "")
            if mt and mt.lower() not in materials_seen:
                materials_seen.add(mt.lower())
                materials_referenced.append(mt)
        lowered_text = full_text.lower()
        for kw in material_keywords:
            if kw in lowered_text and kw not in materials_seen:
                materials_seen.add(kw)
                materials_referenced.append(kw)
        materials_referenced.sort()

        return {
            "status": "success",
            "action": "specification_analysis",
            "file_name": Path(file_path).name if file_path else "extracted_text",
            "divisions_found": detected_divisions,
            "division_filter_applied": division_filter,
            "total_sections_analyzed": len(spec_items),
            "spec_items": spec_items,
            "grade_requirements": grade_requirements,
            "material_specs": material_specs,
            "compliance_flags": compliance_flags,
            "materials_referenced": materials_referenced,
            "methods_specified": [],
            "testing_requirements": testing_requirements,
            "qa_qc_requirements": qa_qc_requirements,
            "standards_referenced": result.get("standards_referenced", []),
        }
    async def safety_compliance_audit(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
    
        audit_type = p.get("audit_type", "general")
        photos = data.get("photos", p.get("photos", []))
    
        if not photos and data.get("file_path"):
            photos = [data.get("file_path")]
    
        if not photos:
            return {
                "status": "error",
                "error": (
                    "No site photos supplied — provide a 'photos' list or a "
                    "'file_path' for image-based safety compliance analysis"
                ),
                "audit_type": audit_type,
            }
    
        violations = []
        compliant_items = []
    
        for photo_path in photos[:10]:
            analysis = await self._analyze_safety_photo(photo_path, audit_type)
        
            if analysis.get("hazards_detected", 0) > 0:
                violations.extend(analysis.get("hazards", []))
            else:
                compliant_items.append({
                    "photo": analysis.get("photo"),
                    "status": "compliant",
                    "notes": "No obvious violations detected"
                })
    
        severity_counts = {"critical": 0, "major": 0, "minor": 0}
        for v in violations:
            sev = v.get("severity", "minor")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
    
        return {
            "status": "success",
            "action": "safety_audit",
            "audit_type": audit_type,
            "photos_analyzed": len(photos),
            "violations_found": len(violations),
            "severity_breakdown": severity_counts,
            "violations": violations[:20],
            "compliant_items": compliant_items,
            "overall_compliance": "fail" if severity_counts["critical"] > 0 else "pass with observations" if severity_counts["major"] > 0 else "pass",
            "recommendations": self._generate_safety_recommendations(violations)
        }
    async def _analyze_safety_photo(self, photo_path: str, audit_type: str) -> Dict:
        image_block = self.get_dep("image")
        safety_prompts = {
            "general": "Identify safety hazards: missing PPE, trip hazards, exposed edges, improper storage",
            "scaffolding": "Check: guardrails, midrails, toeboards, plank overhang, base plates, access",
            "excavation": "Check: shoring, sloping, benching, spoil pile distance, access/egress",
            "electrical": "Check: exposed wires, GFCI, panel access, temporary power, grounding",
            "fall_protection": "Check: guardrails, harnesses, anchor points, lifelines, hole covers"
        }
    
        if image_block:
            try:
                analysis = await image_block.execute(
                    {"file_path": photo_path},
                    {"prompt": safety_prompts.get(audit_type, safety_prompts["general"])}
                )
                desc = analysis.get("result", {}).get("description", "")
            except Exception:
                desc = ""
        else:
            desc = ""
    
        hazards_found = self._parse_safety_hazards(desc)
        return {
            "photo": Path(photo_path).name,
            "hazards_detected": len(hazards_found),
            "hazards": hazards_found,
            "overall_assessment": "unsafe" if hazards_found else "compliant",
            "requires_immediate_action": any(h.get("severity") == "critical" for h in hazards_found)
        }
    async def as_built_deviation_report(self, input_data: Any, params: Dict) -> Dict:
        """Compare as-built conditions against design drawings."""
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}

        as_built_file = data.get("as_built_file") or p.get("as_built_file")
        design_file = data.get("design_file") or p.get("design_file")
        tolerance_mm = float(p.get("tolerance_mm", 10))
        element_type = p.get("element_type", "general")

        deviations = []

        if as_built_file and design_file:
            deviations = await self._compare_as_built_to_design(as_built_file, design_file, tolerance_mm)
        elif data.get("measurements") or p.get("as_built_measurements"):
            as_built_m = data.get("measurements") or p.get("as_built_measurements", [])
            design_m = p.get("design_measurements", [])
            deviations = self._compare_measurement_sets(as_built_m, design_m, tolerance_mm)

        critical = [d for d in deviations if d.get("severity") == "critical"]
        major = [d for d in deviations if d.get("severity") == "major"]
        minor = [d for d in deviations if d.get("severity") == "minor"]

        return {
            "status": "success",
            "action": "as_built_deviation_report",
            "tolerance_mm": tolerance_mm,
            "element_type": element_type,
            "deviation_summary": {
                "total_deviations": len(deviations),
                "critical": len(critical),
                "major": len(major),
                "minor": len(minor),
                "conformance_percent": round(
                    (1 - len(deviations) / max(len(deviations) + 20, 1)) * 100, 1
                ),
            },
            "deviations": deviations[:50],
            "critical_items": critical,
            "recommendations": (
                ["Halt work on affected areas — critical deviations require structural engineer review"]
                if critical
                else ["Major deviations require rectification before next inspection"] if major
                else ["Minor deviations within acceptable tolerance — document and close"]
            ),
            "sign_off_status": (
                "REJECTED" if critical
                else "CONDITIONAL" if major
                else "APPROVED"
            ),
        }
    def _compare_measurement_sets(
        self, as_built: List[Dict], design: List[Dict], tolerance_mm: float
    ) -> List[Dict]:
        deviations = []
        for ab in as_built:
            ab_val = float(ab.get("value", 0))
            matching = next(
                (d for d in design if d.get("type") == ab.get("type")), None
            )
            if matching:
                design_val = float(matching.get("value", 0))
                diff = abs(ab_val - design_val)
                if diff > tolerance_mm / 1000:
                    deviations.append({
                        "element": ab.get("raw", ab.get("type", "Unknown")),
                        "design_value": f"{design_val}{ab.get('unit', '')}",
                        "as_built_value": f"{ab_val}{ab.get('unit', '')}",
                        "deviation": round(diff, 3),
                        "severity": "major" if diff > tolerance_mm / 500 else "minor",
                        "action_required": "Review and document",
                    })
        return deviations
    async def bim_analysis(self, input_data: Any, params: Dict) -> Dict:
        """Analyse a BIM / IFC model for element counts, quantities, and issues.

        Delegates genuine IFC parsing to the bim_extractor block — no demo mode,
        no fabricated quantities. A missing or bad IFC file returns an error.
        """
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}

        ifc_file = data.get("ifc_file") or data.get("file_path") or p.get("ifc_file") or p.get("file_path")
        if not ifc_file:
            return {
                "status": "error",
                "action": "bim_analysis",
                "error": "No IFC file provided — pass ifc_file or file_path pointing to an .ifc model",
            }

        block = self._get_bim_extractor_block()
        if block is None:
            return {"status": "error", "action": "bim_analysis", "error": "bim_extractor block unavailable"}

        result = await block.process({"file_path": ifc_file}, p)
        if not isinstance(result, dict) or result.get("status") != "success":
            return {
                "status": "error",
                "action": "bim_analysis",
                "error": (result or {}).get("error", "bim_extractor failed") if isinstance(result, dict) else "bim_extractor failed",
            }

        # Real, block-extracted data — remap into the bim_analysis response shape.
        quantities = result.get("quantities", {})
        element_count = result.get("element_count", 0)
        # Per-category counts straight from the block's quantities tally.
        element_counts = {cat: q.get("count", 0) for cat, q in quantities.items()}
        # extracted_quantities keeps the block's full per-category breakdown.
        extracted_quantities = quantities
        # Disciplines derived from the real categories present, not synthesised.
        disciplines = sorted(quantities.keys())

        # Floor area from real slab quantities where the IFC exposes areas.
        floor_area = 0.0
        for slab in quantities.get("slabs", {}).get("items", []):
            floor_area += slab.get("netarea") or slab.get("grossarea") or 0

        return {
            "status": "success",
            "action": "bim_analysis",
            "file": ifc_file,
            "model_summary": {
                "total_elements": element_count,
                "disciplines": disciplines,
                "ifc_schema": result.get("ifc_schema", ""),
            },
            "project_info": result.get("project_info", {}),
            "storeys": result.get("storeys", []),
            "spaces": result.get("spaces", []),
            "element_counts": element_counts,
            "extracted_quantities": extracted_quantities,
            "estimated_floor_area_m2": round(floor_area, 2),
            "clash_report": result.get("clash_report", {}),
            "recommendations": [
                "Run clash detection to identify coordination issues",
                "Export quantities to BOQ for cost estimation",
                "Verify element count against design intent — model completeness check recommended",
            ],
        }
    def _extract_measurements_advanced(self, text: str, text_dict: Dict) -> List[Dict]:
        measurements = []

        # WxH dimension pattern: "5.5m x 3.2m"
        dimension_pattern = r'\b(\d+(?:\.\d+)?)\s*(?:m|m\.|meter|meters|ft|feet|foot|\')\s*(?:x|by|×)\s*(\d+(?:\.\d+)?)\s*(?:m|m\.|meter|meters|ft|feet|foot|\')'
        for match in re.finditer(dimension_pattern, text, re.IGNORECASE):
            width = float(match.group(1))
            height = float(match.group(2))
            unit = "m" if "m" in match.group(0).lower() else "ft"
            area = width * height
            measurements.append({
                "type": "dimension",
                "value": area,
                "unit": f"{unit}²",
                "width": width,
                "height": height,
                "raw": match.group(0),
                "context": text[max(0, match.start()-50):match.end()+50]
            })

        # Direct area mentions: "2500 m2", "floor area: 2,500 sqm"
        area_pattern = r'\b(\d[\d,]*(?:\.\d+)?)\s*(?:m2|m²|sqm|sq\.?\s*m|square\s+met(?:re|er)s?)\b'
        for match in re.finditer(area_pattern, text, re.IGNORECASE):
            try:
                val = float(match.group(1).replace(',', ''))
                if val > 0:
                    measurements.append({
                        "type": "dimension",
                        "value": val,
                        "unit": "m²",
                        "raw": match.group(0),
                        "context": text[max(0, match.start()-50):match.end()+50]
                    })
            except ValueError:
                pass

        # Direct volume mentions: "450 m3", "concrete: 450 m³"
        volume_pattern = r'\b(\d[\d,]*(?:\.\d+)?)\s*(?:m3|m³|cubic\s+met(?:re|er)s?)\b'
        for match in re.finditer(volume_pattern, text, re.IGNORECASE):
            try:
                val = float(match.group(1).replace(',', ''))
                if val > 0:
                    measurements.append({
                        "type": "volume",
                        "value": val,
                        "unit": "m³",
                        "raw": match.group(0),
                        "context": text[max(0, match.start()-50):match.end()+50]
                    })
            except ValueError:
                pass

        quantity_pattern = r'\b(\d+)\s*(?:no|nos|nr|ea|each)?\.?\s*([A-Z][A-Za-z\s]+)'
        for match in re.finditer(quantity_pattern, text[:2000]):
            qty = int(match.group(1))
            item = match.group(2).strip()[:50]
            if len(item) > 3:
                measurements.append({
                    "type": "count",
                    "value": qty,
                    "unit": "ea",
                    "item": item,
                    "raw": match.group(0)
                })

        return measurements[:50]
    async def _detect_risks_from_drawing(self, result: Dict) -> List[Dict]:
        risks = []

        if not result.get("measurements"):
            risks.append({
                "type": "data_quality",
                "description": "No measurements detected — manual verification required",
                "severity": "medium",
                "mitigation": "Use quantity surveyor to verify BOQ",
            })

        if result.get("confidence", {}).get("overall", 1.0) < 0.7:
            risks.append({
                "type": "confidence",
                "description": "Low extraction confidence — OCR or PDF quality may be poor",
                "severity": "medium",
                "mitigation": "Review all quantities manually against original drawings",
            })

        disciplines = result.get("detected_disciplines", [])
        if len(disciplines) > 3:
            risks.append({
                "type": "coordination",
                "description": f"Multiple disciplines detected ({', '.join(disciplines)}) — coordination drawings required",
                "severity": "low",
                "mitigation": "Conduct BIM coordination review before construction",
            })

        specs = result.get("specifications", [])
        high_grade = [s for s in specs if any(g in s.get("value", "") for g in ["C50", "C60", "S460", "S500"])]
        if high_grade:
            risks.append({
                "type": "specification",
                "description": f"High-strength materials specified ({', '.join(s['value'] for s in high_grade[:3])}) — specialist procurement required",
                "severity": "medium",
                "mitigation": "Verify supplier availability and lead times early",
            })

        quantities = result.get("quantities", {})
        if quantities.get("concrete_volume_m3", 0) > 5000:
            risks.append({
                "type": "procurement",
                "description": "Large concrete volume — ready-mix supply continuity risk",
                "severity": "medium",
                "mitigation": "Secure supply agreement with ready-mix plant before construction start",
            })

        return risks
    async def bim_clash_detection(self, input_data: Any, params: Dict) -> Dict:
        """Detect clashes in BIM / IFC discipline models.

        Delegates to the bim_extractor block, which runs a real intra-model
        clash report per IFC file. No demo mode, no fabricated clashes — a
        missing or bad IFC file returns an error.
        """
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}

        ifc_file = data.get("ifc_file") or p.get("ifc_file") or data.get("file_path") or p.get("file_path")
        discipline_models = list(p.get("discipline_models") or data.get("discipline_models", []))
        if ifc_file and ifc_file not in discipline_models:
            discipline_models = [ifc_file] + discipline_models

        if not discipline_models:
            return {
                "status": "error",
                "action": "bim_clash_detection",
                "error": "No IFC file provided — pass ifc_file, file_path, or discipline_models pointing to .ifc models",
            }

        block = self._get_bim_extractor_block()
        if block is None:
            return {"status": "error", "action": "bim_clash_detection", "error": "bim_extractor block unavailable"}

        # The block runs an intra-model clash report per file; aggregate across
        # the supplied discipline models. Cross-model clashing is not fabricated.
        block_params = dict(p)
        block_params["run_clash_detection"] = True
        clashes: List[Dict] = []
        total_elements = 0
        models_processed: List[str] = []
        detection_method = "name_duplicate_proxy"
        for model_file in discipline_models:
            result = await block.process({"file_path": model_file}, block_params)
            if not isinstance(result, dict) or result.get("status") != "success":
                return {
                    "status": "error",
                    "action": "bim_clash_detection",
                    "error": (result or {}).get("error", f"bim_extractor failed for {model_file}") if isinstance(result, dict) else "bim_extractor failed",
                }
            models_processed.append(model_file)
            total_elements += result.get("element_count", 0)
            clash_report = result.get("clash_report", {})
            detection_method = clash_report.get("detection_method", detection_method)
            for c in clash_report.get("clashes", []):
                clashes.append(self._normalize_block_clash(c, model_file))

        by_discipline = self._group_clashes_by_discipline(clashes)
        clash_ratio = len(clashes) / total_elements if total_elements else 0

        return {
            "status": "success",
            "action": "clash_detection",
            "model_summary": {
                "files_analyzed": models_processed,
                "total_elements_checked": total_elements,
                "models_clashed": len(models_processed),
            },
            "clash_summary": {
                "total_clashes": len(clashes),
                "warnings": len([c for c in clashes if c["severity"] == "warning"]),
                "clash_ratio_percent": round(clash_ratio * 100, 2),
                "detection_method": detection_method,
            },
            "clashes": clashes[:100] if not p.get("full_report") else clashes,
            "by_discipline": by_discipline,
            "coordination_meeting_agenda": self._generate_coordination_agenda(clashes),
        }
    async def om_manual_generator(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        equipment_list = data.get("equipment_list") or p.get("equipment_list", [])
        spec_file = data.get("spec_file") or p.get("spec_file")
        as_built_drawings = data.get("drawings") or p.get("drawings", [])
        commissioning_data = data.get("commissioning") or p.get("commissioning", {})
        project_name = p.get("project_name", "Project")
    
        if not equipment_list:
            equipment_list = [
                {"tag": "HVAC-01", "description": "AHU-1 Air Handling Unit", "system_type": "HVAC", "manufacturer": "TBC", "model": "TBC", "location": "Roof Level", "warranty_years": 2},
                {"tag": "HVAC-02", "description": "Chiller Unit CHL-1", "system_type": "HVAC", "manufacturer": "TBC", "model": "TBC", "location": "Plant Room", "warranty_years": 2},
                {"tag": "ELEC-01", "description": "Main LV Switchboard", "system_type": "Electrical", "manufacturer": "TBC", "model": "TBC", "location": "Ground Floor", "warranty_years": 1},
                {"tag": "ELEC-02", "description": "Emergency Generator", "system_type": "Electrical", "manufacturer": "TBC", "model": "TBC", "location": "Basement", "warranty_years": 2},
                {"tag": "PLMB-01", "description": "Booster Pump Set", "system_type": "Plumbing", "manufacturer": "TBC", "model": "TBC", "location": "Pump Room", "warranty_years": 1},
                {"tag": "FIRE-01", "description": "Fire Alarm Panel", "system_type": "Fire Protection", "manufacturer": "TBC", "model": "TBC", "location": "Reception", "warranty_years": 1},
                {"tag": "LIFT-01", "description": "Passenger Lift 1", "system_type": "Vertical Transport", "manufacturer": "TBC", "model": "TBC", "location": "Core", "warranty_years": 2},
            ]
    
        sections = []
        sections.append({
            "section": "A. Project Information",
            "content": {
                "project_name": project_name,
                "completion_date": commissioning_data.get("completion_date", "TBD"),
                "contractor": commissioning_data.get("contractor", "TBD"),
                "consultants": commissioning_data.get("consultants", []),
                "warranty_periods": commissioning_data.get("warranties", {}),
                "emergency_contacts": commissioning_data.get("emergency_contacts", [])
            }
        })
    
        systems = self._group_equipment_by_system(equipment_list)
        sections.append({
            "section": "B. Systems Overview",
            "content": {
                "system_descriptions": [{"name": s["name"], "description": s["description"], "components": len(s["equipment"])} for s in systems],
                "system_interdependencies": self._map_system_dependencies(systems)
            }
        })
    
        equipment_data = []
        for equip in equipment_list:
            equipment_data.append({
                "tag_number": equip.get("tag", "TBD"),
                "description": equip.get("description"),
                "manufacturer": equip.get("manufacturer"),
                "model": equip.get("model"),
                "serial_number": equip.get("serial", "To be field verified"),
                "location": equip.get("location"),
                "installation_date": equip.get("install_date"),
                "warranty_expiry": self._add_years_str(equip.get("install_date"), equip.get("warranty_years", 1)),
                "performance_data": equip.get("performance", {}),
                "rated_capacity": equip.get("capacity"),
                "electrical_requirements": equip.get("electrical", {}),
                "maintenance_schedule": self._generate_equipment_maintenance(equip)
            })
    
        sections.append({"section": "C. Equipment Schedules & Technical Data", "content": equipment_data})
        sections.append({"section": "D. Operating Procedures", "content": {"startup_procedures": self._generate_startup_procedures(systems), "normal_operation": self._generate_normal_operation(systems), "shutdown_procedures": self._generate_shutdown_procedures(systems), "emergency_procedures": self._generate_emergency_procedures(systems), "seasonal_operation": self._generate_seasonal_operation(systems)}})
        sections.append({"section": "E. Preventive Maintenance", "content": {"daily_tasks": self._generate_daily_tasks(equipment_list), "weekly_tasks": self._generate_weekly_tasks(equipment_list), "monthly_tasks": self._generate_monthly_tasks(equipment_list), "quarterly_tasks": self._generate_quarterly_tasks(equipment_list), "annual_tasks": self._generate_annual_tasks(equipment_list), "maintenance_matrix": self._create_maintenance_matrix(equipment_list)}})
        sections.append({"section": "F. Troubleshooting Guide", "content": self._generate_troubleshooting_guide(equipment_list)})
        sections.append({"section": "G. As-Built Documentation", "content": {"drawings_list": [Path(d).name for d in as_built_drawings], "specifications_reference": spec_file if spec_file else "Refer to contract documents", "test_results": commissioning_data.get("test_results", []), "certificates": commissioning_data.get("certificates", [])}})
        sections.append({"section": "H. Warranties & Spare Parts", "content": {"warranty_register": [{"equipment": e.get("description") or e.get("name", "TBD"), "expiry": e.get("warranty_expiry"), "contact": e.get("supplier_contact")} for e in equipment_list], "recommended_spare_parts": self._generate_spare_parts_list(equipment_list), "supplier_contacts": list(set([e.get("supplier_contact") for e in equipment_list if e.get("supplier_contact")]))}})
    
        manual_metadata = {
            "document_number": f"OM-{project_name.replace(' ', '-')}-{datetime.now(timezone.utc).year}",
            "revision": "00 - First Issue",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_pages_estimate": len(equipment_list) * 3 + 50,
            "prepared_by": commissioning_data.get("contractor", "Contractor"),
            "approved_by": "Consultant/Client",
            "distribution": ["Client", "Facilities Management", "Building Operator"]
        }
    
        return {
            "status": "success",
            "action": "om_manual_generated",
            "manual_metadata": manual_metadata,
            "sections": sections,
            "summary": {
                "total_equipment": len(equipment_list),
                "systems_covered": len(systems),
                "warranty_items": len(equipment_list),
                "maintenance_tasks_generated": len(sections[4]["content"]["daily_tasks"]) + len(sections[4]["content"]["monthly_tasks"]),
                "estimated_manual_pages": manual_metadata["total_pages_estimate"]
            },
            "digital_format": {
                "recommended_software": "PDF with hyperlinks, or CAFM system integration",
                "hyperlink_structure": "Section-based navigation with equipment tags linked to data sheets",
                "update_procedure": "Annual review or upon equipment replacement"
            },
            "training_materials": self._extract_training_needs(equipment_list),
            "appendices": [
                "Equipment Data Sheets", "Test Reports", "Certificates", "Spare Parts Lists", "Supplier Contacts"
            ]
        }
    async def digital_twin_sync(self, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        twin_platform = p.get("platform", "generic")
        sync_mode = p.get("mode", "update")
        project_id = p.get("project_id", "project_001")
        data_payload = data.get("data") or p.get("data", {})
    
        transformed_data = self._transform_for_platform(data_payload, twin_platform)
    
        if sync_mode == "initial_sync":
            operations = self._generate_initial_sync_operations(transformed_data, twin_platform)
        elif sync_mode == "delta_sync":
            operations = self._generate_delta_operations(transformed_data, twin_platform)
        else:
            operations = self._generate_update_operations(transformed_data, twin_platform)
    
        platform_config = self._get_platform_config(twin_platform, project_id)
        quality_report = self._check_twin_data_quality(transformed_data)
        api_payloads = self._generate_api_payloads(operations, twin_platform)
    
        return {
            "status": "success",
            "action": "digital_twin_sync",
            "platform": twin_platform,
            "sync_mode": sync_mode,
            "project_id": project_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_summary": {
                "elements_to_sync": len(operations),
                "data_points": sum(len(op.get("properties", [])) for op in operations),
                "geometry_updates": len([op for op in operations if op.get("type") == "geometry"]),
                "property_updates": len([op for op in operations if op.get("type") == "property"]),
                "relationship_updates": len([op for op in operations if op.get("type") == "relationship"])
            },
            "operations": operations[:50] if not p.get("full_details") else operations,
            "platform_configuration": platform_config,
            "api_payloads": api_payloads[:10] if not p.get("include_payloads") else api_payloads,
            "data_quality": quality_report,
            "sync_recommendations": self._generate_sync_recommendations(quality_report, twin_platform),
            "connection_strings": {
                "bim360": f"https://developer.api.autodesk.com/modelderivative/v2/designdata/{project_id}",
                "azure": f"https://{project_id}.api.weu.digitaltwins.azure.net",
                "aveva": f"connect.aveva.com/{project_id}",
                "generic": "Custom API endpoint required"
            }.get(twin_platform, "Platform-specific endpoint required"),
            "authentication_required": {
                "type": "OAuth2" if twin_platform in ["bim360", "azure"] else "API Key",
                "scope": "Digital Twin Read/Write"
            }
        }
    async def intelligent_workflow(self, input_data: Any, params: Dict) -> Dict:
        """Smart orchestrator - auto-detects user intent and chains actions"""
        user_goal = params.get("goal") or params.get("prompt", "process document")
        data = input_data if isinstance(input_data, dict) else {}
        file_path = data.get("file_path") or data.get("url")
    
        chain_steps = self._build_intelligent_chain(user_goal, file_path)
        results = []
        current_data = input_data
    
        for step in chain_steps:
            method = getattr(self, step["action"], None)
            if method:
                result = await method(current_data, step.get("params", {}))
                results.append({
                    "step": step["action"],
                    "status": result.get("status"),
                    "key_findings": self._extract_key_findings(result)
                })
                current_data = {**(current_data if isinstance(current_data, dict) else {}), "previous_result": result}
    
        next_action = self._suggest_next_action(results, user_goal)
    
        return {
            "status": "success",
            "action": "intelligent_workflow",
            "workflow_executed": [s["action"] for s in chain_steps],
            "step_results": results,
            "consolidated_summary": self._consolidate_results(results),
            "next_recommended_action": next_action,
            "user_query": user_goal
        }
    def _build_intelligent_chain(self, user_goal: str, file_path: Optional[str]) -> List[Dict]:
        """Determine which construction methods to call based on user intent"""
        goal = user_goal.lower()
        chain = []
    
        if file_path and file_path.endswith('.pdf'):
            if any(k in goal for k in ["drawing", "plan", "elevation", "section"]):
                chain.append({"action": "process_document", "params": {"doc_type": "drawing"}})
            elif any(k in goal for k in ["spec", "specification", "csi", "masterformat"]):
                chain.append({"action": "process_specification_full", "params": {}})
            elif any(k in goal for k in ["contract", "clause", "terms", "risk"]):
                chain.append({"action": "process_contract", "params": {}})
            else:
                chain.append({"action": "process_document", "params": {}})
    
        if any(k in goal for k in ["qto", "quantity", "takeoff", "boq", "measurement", "material estimate"]):
            chain.append({"action": "extract_quantities", "params": {}})
    
        if any(k in goal for k in ["cost", "price", "budget", "estimate", "value"]):
            chain.append({"action": "estimate_costs", "params": {}})
    
        if any(k in goal for k in ["buy", "purchase", "procure", "supplier", "enquiry", "order", "lead time"]):
            if not any(s["action"] == "extract_quantities" for s in chain):
                chain.append({"action": "extract_quantities", "params": {}})
            chain.append({"action": "procurement_optimizer", "params": {}})
    
        if any(k in goal for k in ["schedule", "programme", "primavera", "delay", "critical path", "progress"]):
            chain.append({"action": "parse_primavera_schedule", "params": {}})
    
        if any(k in goal for k in ["delay analysis", "forensic", "time impact", "extension of time", "eot", "claim"]):
            chain.append({"action": "forensic_delay_analysis", "params": {}})
            chain.append({"action": "claims_builder", "params": {}})
    
        if any(k in goal for k in ["variation", "change order", "vo", "additional work", "omission"]):
            chain.append({"action": "change_order_impact", "params": {}})
            chain.append({"action": "variation_order_manager", "params": {}})
    
        if any(k in goal for k in ["cash flow", "s-curve", "payment", "invoice", "billing"]):
            chain.append({"action": "cash_flow_forecast", "params": {}})
            chain.append({"action": "payment_certificate", "params": {}})
    
        if any(k in goal for k in ["quality", "defect", "inspection", "qc", "honeycomb", "crack"]):
            chain.append({"action": "qa_qc_inspection", "params": {}})
    
        if any(k in goal for k in ["safety", "osha", "hazard", "incident", "audit"]):
            chain.append({"action": "safety_compliance_audit", "params": {}})
    
        if any(k in goal for k in ["tender", "bid", "bid evaluation", "contractor selection", "quote comparison"]):
            chain.append({"action": "tender_bid_analysis", "params": {}})
    
        if any(k in goal for k in ["carbon", "co2", "green", "esg", "sustainability", "leed", "breeam"]):
            chain.append({"action": "carbon_footprint_calculator", "params": {}})
            chain.append({"action": "esg_sustainability_report", "params": {}})
    
        if any(k in goal for k in ["value engineering", "ve", "alternative", "substitution", "saving", "optimization"]):
            chain.append({"action": "value_engineering", "params": {}})
    
        if any(k in goal for k in ["commissioning", "handover", "practical completion", "testing"]):
            chain.append({"action": "commissioning_checklist", "params": {}})
    
        if any(k in goal for k in ["o&m", "operation and maintenance", "manual", "warranty", "maintenance schedule"]):
            chain.append({"action": "om_manual_generator", "params": {}})
            chain.append({"action": "warranty_maintenance_schedule", "params": {}})
    
        if any(k in goal for k in ["as built", "as-built", "deviation", "record drawing"]):
            chain.append({"action": "as_built_deviation_report", "params": {}})
    
        if any(k in goal for k in ["bim", "clash", "coordination", "model"]):
            chain.append({"action": "bim_clash_detection", "params": {}})
    
        if any(k in goal for k in ["digital twin", "sync", "iot", "sensor"]):
            chain.append({"action": "digital_twin_sync", "params": {}})
    
        if any(k in goal for k in ["submittal", "shop drawing", "sample", "mockup", "approval"]):
            chain.append({"action": "submittal_log_generator", "params": {}})
    
        if any(k in goal for k in ["labor", "manpower", "resource", "histogram", "loading"]):
            chain.append({"action": "resource_histogram", "params": {}})
    
        if any(k in goal for k in ["rfi", "request for information", "clarification", "ambiguity"]):
            chain.append({"action": "rfi_generator", "params": {}})
    
        if any(k in goal for k in ["risk", "risk register", "mitigation", "contingency"]):
            chain.append({"action": "risk_register_auto_populate", "params": {}})
    
        if any(k in goal for k in ["daily report", "site diary", "daily log", "progress photo"]):
            chain.append({"action": "daily_site_report", "params": {}})
    
        if not chain:
            chain.append({"action": "process_document", "params": {}})
    
        return chain
    async def _analyse_text_only(self, text: str, doc_type_hint: str = "auto") -> Dict:
        """Classify and extract structured data from raw text without a file."""
        t = text.lower()

        # Detect doc type from content
        if doc_type_hint != "auto":
            doc_type = doc_type_hint
        elif any(k in t for k in ["bill of quantities", "boq", "schedule of rates", "item no", "unit rate"]):
            doc_type = "bom"
        elif any(k in t for k in ["specification", "clause", "section", "csi", "masterformat", "div "]):
            doc_type = "specification"
        elif any(k in t for k in ["contract", "agreement", "clause", "liquidated damages", "retention"]):
            doc_type = "contract"
        elif any(k in t for k in ["programme", "schedule", "activity id", "wbs", "baseline", "primavera"]):
            doc_type = "schedule"
        elif any(k in t for k in ["drawing", "elevation", "section", "plan", "detail", "grid"]):
            doc_type = "drawing"
        else:
            doc_type = "report"

        # Extract quantities from text
        import re
        quantities = {}
        patterns = [
            (r"concrete[^\n]*?(\d[\d,\.]*)\s*m3", "concrete_m3", "m3"),
            (r"rebar[^\n]*?(\d[\d,\.]*)\s*kg", "rebar_kg", "kg"),
            (r"reinforcement[^\n]*?(\d[\d,\.]*)\s*kg", "rebar_kg", "kg"),
            (r"steel[^\n]*?(\d[\d,\.]*)\s*kg", "structural_steel_kg", "kg"),
            (r"curtain wall[^\n]*?(\d[\d,\.]*)\s*m2", "curtain_wall_m2", "m2"),
            (r"glazing[^\n]*?(\d[\d,\.]*)\s*m2", "glazing_m2", "m2"),
            (r"hvac[^\n]*?(\d[\d,\.]*)\s*m2", "hvac_m2", "m2"),
            (r"electrical[^\n]*?(\d[\d,\.]*)\s*m2", "electrical_m2", "m2"),
            (r"blockwork[^\n]*?(\d[\d,\.]*)\s*m2", "blockwork_m2", "m2"),
            (r"formwork[^\n]*?(\d[\d,\.]*)\s*m2", "formwork_m2", "m2"),
            (r"excavat[^\n]*?(\d[\d,\.]*)\s*m3", "excavation_m3", "m3"),
            (r"pil[^\n]*?(\d[\d,\.]*)\s*lm", "piling_lm", "lm"),
            (r"waterproof[^\n]*?(\d[\d,\.]*)\s*m2", "waterproofing_m2", "m2"),
            (r"roofing[^\n]*?(\d[\d,\.]*)\s*m2", "roofing_m2", "m2"),
            (r"tiling[^\n]*?(\d[\d,\.]*)\s*m2", "tiling_m2", "m2"),
            (r"painting[^\n]*?(\d[\d,\.]*)\s*m2", "painting_m2", "m2"),
            (r"plumbing[^\n]*?(\d[\d,\.]*)\s*m2", "plumbing_m2", "m2"),
        ]
        for pattern, key, unit in patterns:
            m = re.search(pattern, t)
            if m:
                val = _safe_float(m.group(1).replace(",", ""))
                if val > 0:
                    quantities[key] = {"quantity": val, "unit": unit}

        # Extract risks from text
        risks = []
        risk_keywords = ["design change", "material delay", "labour shortage", "weather", "cash flow",
                         "subcontractor", "permit", "ground condition", "safety", "covid", "inflation"]
        for rk in risk_keywords:
            if rk in t:
                risks.append({"description": rk.title(), "likelihood": "medium", "impact": "medium"})

        return {
            "status": "success",
            "doc_type": doc_type,
            "quantities": quantities,
            "risks": risks,
            "specifications": [],
            "title": None,
            "project": None,
            "pages": None,
        }
    async def _process_office_document(self, file_path: str, ext: str, extracted_text: str = "") -> Dict:
        """Route .docx / .xlsx through the document_engine and boq_processor blocks.

        The legacy `_process_drawing` path uses fitz/PyMuPDF which only handles
        PDFs and images. This helper produces a doc_result shaped like
        process_document's output (status, doc_type, quantities, risks, ...) so
        auto_pipeline can build panels without special-casing downstream.
        """
        from app.blocks import BLOCK_REGISTRY

        is_xlsx = ext in ("xlsx", "xls")
        is_docx = ext in ("docx", "doc")

        engine_input = {}
        engine_params = {"xlsx_path" if is_xlsx else "docx_path": file_path}

        engine_result = {}
        engine_block = BLOCK_REGISTRY.get("document_engine")
        if engine_block:
            try:
                engine_instance = engine_block()
                engine_result = await engine_instance.execute(engine_input, engine_params)
            except Exception:
                engine_result = {}

        # For BOQ-style spreadsheets, also try boq_processor — it returns
        # priced line items the procurement pipeline can use directly.
        boq_items = []
        boq_summary = {}
        boq_extract_error = ""
        if is_xlsx:
            boq_block = BLOCK_REGISTRY.get("boq_processor")
            if boq_block:
                try:
                    boq_instance = boq_block()
                    boq_result = await boq_instance.execute({"file_path": file_path}, {})
                    if boq_result.get("status") == "success":
                        boq_items = boq_result.get("line_items", []) or []
                        boq_summary = {
                            "item_count": boq_result.get("item_count", 0),
                            "total_cost": boq_result.get("total_cost", 0),
                            "currency": boq_result.get("currency", "USD"),
                            "sections": boq_result.get("sections", []),
                        }
                    else:
                        # Non-success result — capture so the panel can show
                        # a real reason instead of "BOQ loaded" with 0 items.
                        boq_extract_error = (
                            boq_result.get("error")
                            or f"BOQ processor returned status={boq_result.get('status')!r}"
                        )
                        logger.warning(
                            "documents: boq_processor returned non-success for %s: %s",
                            file_path, boq_extract_error,
                        )
                except Exception as exc:
                    logger.exception(
                        "documents: boq_processor.execute raised for %s", file_path,
                    )
                    boq_extract_error = f"BOQ extraction failed: {exc}"

        # Heuristic doc_type: schedule/contract/specification/drawing based on
        # filename and parsed content (consistent with _classify_document).
        name = file_path.lower()
        if any(k in name for k in ("schedule", "primavera", "p6", "_schedule", "l2_schedule", "l3_schedule")):
            doc_type = "schedule"
        elif any(k in name for k in ("contract", "agreement", "rfp", "request for proposal")):
            doc_type = "contract"
        elif any(k in name for k in ("spec", "basis of design", "performance basis")):
            doc_type = "specification"
        elif boq_items:
            doc_type = "bom"
        else:
            doc_type = "specification" if is_docx else "schedule"

        # Build a quantities dict from BOQ line items if we have them
        quantities: Dict[str, Any] = {}
        if boq_items:
            for item in boq_items:
                desc = (item.get("description") or item.get("item") or "").strip()
                qty = item.get("quantity") or 0
                unit = item.get("unit") or "ea"
                if not desc or qty <= 0:
                    continue
                # Use whitelist filter consistent with _calculate_quantities
                key = " ".join(desc.split()).lower().replace(" ", "_")[:40]
                quantities[key] = {"quantity": _safe_float(qty), "unit": unit}

        # Pull risks/requirements from document_engine if present
        risks_raw = engine_result.get("risks", []) if isinstance(engine_result, dict) else []
        risks = []
        for r in risks_raw[:20]:
            if isinstance(r, dict):
                risks.append({
                    "description": r.get("description") or r.get("title") or str(r)[:120],
                    "likelihood": r.get("likelihood", "medium"),
                    "impact": r.get("impact", "medium"),
                })

        equipment_specs = engine_result.get("equipment_specs", []) if isinstance(engine_result, dict) else []
        requirements = engine_result.get("requirements", []) if isinstance(engine_result, dict) else []

        return {
            "status": "success",
            "doc_type": doc_type,
            "quantities": quantities,
            "boq_summary": boq_summary,
            "boq_items": boq_items,
            # Empty string when no error; populated with the failure reason
            # so callers can surface "Failed to read: ..." in the BOQ panel
            # instead of rendering an empty "BOQ loaded" panel.
            "boq_extract_error": boq_extract_error,
            "risks": risks,
            "specifications": [r for r in requirements if isinstance(r, dict)][:50],
            "equipment_specs": equipment_specs,
            "title": None,
            "project": None,
            "pages": None,
            "_engine_result": engine_result,
        }
    async def auto_pipeline(self, input_data: Any, params: Dict) -> Dict:
        """
        Single-call intelligent pipeline.
        1. Runs process_document to understand the file.
        2. Auto-dispatches downstream actions based on what was found.
        3. Returns structured panels ready for UI rendering — no LLM required.
        """
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        file_path = data.get("file_path") or p.get("file_path") or ""
        extracted_text = data.get("extracted_text") or data.get("text") or ""

        if not file_path and not extracted_text:
            return {"status": "error", "error": "Provide file_path or extracted_text"}

        # ── Step 1: domain analysis ──────────────────────────────────────────
        # Detect docx/xlsx up front and route through document_engine, since
        # process_document → _process_drawing uses fitz which only handles PDFs.
        ext = file_path.rsplit(".", 1)[-1].lower() if file_path else ""
        if file_path and ext in ("docx", "doc", "xlsx", "xls"):
            doc_result = await self._process_office_document(file_path, ext, extracted_text)
        elif file_path:
            doc_result = await self.process_document(
                {"file_path": file_path, "extracted_text": extracted_text},
                {"doc_type": p.get("doc_type", "auto"), "file_path": file_path}
            )
        else:
            # Text-only path — classify from content, skip file IO
            doc_type_hint = p.get("doc_type", "auto")
            doc_result = await self._analyse_text_only(extracted_text, doc_type_hint)

        doc_type = doc_result.get("doc_type", "unknown")
        panels = []
        downstream = {}
        next_actions = []
        # Per-panel failures are captured here so the SPA can render a
        # "1 panel failed to populate" notice instead of silently empty
        # sections. Each entry: {"panel": <name>, "error": <message>}.
        pipeline_warnings: List[Dict] = []

        # ── Document info panel (always) ─────────────────────────────────────
        panels.append({
            "type": "document_info",
            "title": "Document",
            "data": {
                "file": file_path.split("/")[-1],
                "doc_type": doc_type,
                "status": doc_result.get("status"),
                "pages": doc_result.get("pages"),
                "title": doc_result.get("title") or doc_result.get("document_title"),
                "project": doc_result.get("project_name") or doc_result.get("project"),
            }
        })

        # ── Step 2: auto-dispatch based on detected content ──────────────────

        # Quantities → cost estimate + procurement
        quantities = (
            doc_result.get("quantities") or
            doc_result.get("extracted_quantities") or
            doc_result.get("bill_of_quantities") or {}
        )

        def _qty_val(q):
            """Normalize quantity value to a number."""
            if isinstance(q, dict):
                return _safe_float(q.get("quantity", 0))
            return _safe_float(q)

        # Only show quantities panel when at least one value is non-zero
        has_quantities = bool(quantities) and any(
            _qty_val(v) > 0 for v in quantities.values()
        )
        if has_quantities:
            panels.append({"type": "quantities", "title": "Quantities", "data": quantities})
        cost_result = {}
        if has_quantities:
            try:
                # Real cost estimate — delegates per-item unit rates to the
                # historical_benchmark block. No fabricated composite $/m² rate.
                cost_result = await self.generate_cost_estimate(
                    {"quantities": quantities},
                    {
                        "quantities": quantities,
                        "location": p.get("location", "US National Average"),
                        "project_type": p.get("project_type", "general_building"),
                    },
                )
                if isinstance(cost_result, dict) and cost_result.get("status") == "success":
                    downstream["cost_estimate"] = cost_result
                    panels.append({
                        "type": "cost_estimate",
                        "title": "Cost Estimate",
                        "data": cost_result.get("summary", {}),
                        "line_items": cost_result.get("line_items", []),
                        "unpriced_items": cost_result.get("unpriced_items", []),
                    })
                else:
                    # Estimate failed — surface the reason honestly, no fake number.
                    downstream["cost_estimate"] = cost_result
                    panels.append({
                        "type": "cost_estimate",
                        "title": "Cost Estimate",
                        "data": {},
                        "line_items": [],
                        "unpriced_items": [],
                        "error": (cost_result or {}).get(
                            "error", "Cost estimate unavailable"
                        ) if isinstance(cost_result, dict) else "Cost estimate unavailable",
                    })
            except Exception as exc:
                logger.exception("auto_pipeline: cost estimate calculation failed")
                pipeline_warnings.append({"panel": "cost_estimate", "error": str(exc)})
                # Surface the failure honestly rather than silently dropping
                # the panel — consistent with the else-branch above.
                panels.append({
                    "type": "cost_estimate",
                    "title": "Cost Estimate",
                    "data": {},
                    "line_items": [],
                    "unpriced_items": [],
                    "error": f"Cost estimate failed: {exc}",
                })
        # Procurement: if we extracted real quantities, derive the procurement
        # list inline so the user sees it without having to click another button.
        # Otherwise just expose the button for manual triggering.
        if has_quantities:
            try:
                proc_result = await self.procurement_list_generator(
                    {"quantities": quantities, "schedule_start": p.get("schedule_start")},
                    {"budget": p.get("budget")}
                )
                items = proc_result.get("procurement_list", []) or []
                if items:
                    downstream["procurement_list"] = proc_result
                    panels.append({
                        "type": "procurement",
                        "title": "Procurement List",
                        "data": {
                            "procurement_list": items,
                            "total_items": proc_result.get("total_items"),
                            "total_procurement_cost": proc_result.get("total_procurement_cost"),
                            "critical_long_lead_items": proc_result.get("critical_long_lead_items"),
                            "action_required": proc_result.get("action_required", []),
                        },
                    })
            except Exception as exc:
                logger.exception("auto_pipeline: procurement_list_generator failed")
                pipeline_warnings.append({"panel": "procurement", "error": str(exc)})
        next_actions.append({
            "action": "procurement_list_generator",
            "label": "Generate Procurement List",
            "reason": "Re-run procurement scheduling with custom budget / start date"
        })

        # Risks → risk register
        risks = doc_result.get("risks") or doc_result.get("identified_risks") or []
        if risks or doc_type in ("contract", "drawing", "specification"):
            try:
                risk_result = await self.risk_register_auto_populate(
                    {"auto_risks": risks, "project_type": p.get("project_type", "general_building")},
                    {"location": p.get("location", "US National Average")}
                )
                downstream["risk_register"] = risk_result
                panels.append({
                    "type": "risks",
                    "title": "Risk Register",
                    "data": risk_result.get("risks", []),
                    "total": risk_result.get("total_risks", 0)
                })
            except Exception as exc:
                logger.exception("auto_pipeline: risk_register_auto_populate failed")
                pipeline_warnings.append({"panel": "risks", "error": str(exc)})

        # Specifications → submittal log
        specs = doc_result.get("specifications") or doc_result.get("spec_sections") or []
        if specs or doc_type == "specification":
            try:
                submittal_result = await self.submittal_log_generator(
                    {"specifications": specs, "file_path": file_path},
                    {}
                )
                downstream["submittal_log"] = submittal_result
                panels.append({
                    "type": "submittals",
                    "title": "Submittal Log",
                    "data": submittal_result.get("submittals", []),
                    "total": submittal_result.get("total_submittals", 0)
                })
            except Exception as exc:
                logger.exception("auto_pipeline: submittal_log_generator failed")
                pipeline_warnings.append({"panel": "submittals", "error": str(exc)})

        # Schedule → progress tracker
        if doc_type == "schedule":
            ext_for_sched = file_path.rsplit(".", 1)[-1].lower() if file_path else ""
            if ext_for_sched == "xer":
                # Primavera P6 — use the dedicated parser
                try:
                    sched_result = await self.parse_primavera_schedule(
                        {"file_path": file_path}, {}
                    )
                    if sched_result.get("status") != "error":
                        downstream["schedule"] = sched_result
                        panels.append({
                            "type": "schedule",
                            "title": "Schedule",
                            "data": sched_result,
                        })
                        next_actions.append({
                            "action": "progress_tracker",
                            "label": "Track Progress",
                            "reason": "Schedule loaded",
                        })
                except Exception as exc:
                    logger.exception("auto_pipeline: parse_primavera_schedule failed")
                    pipeline_warnings.append({"panel": "schedule", "error": str(exc)})
                # Excel schedule — build a summary panel from what document_engine
                # already extracted, plus a quick row scan for date columns.
                eng = doc_result.get("_engine_result") if isinstance(doc_result, dict) else None
                eng = eng if isinstance(eng, dict) else {}
                xlsx_summary = {
                    "format": "xlsx",
                    "file": file_path.split("/")[-1],
                    "schedule_targets": eng.get("schedule_targets", []),
                    "equipment_specs": eng.get("equipment_specs", []),
                    "constraints": eng.get("constraints", [])[:10],
                    "requirements_count": len(eng.get("requirements", [])),
                }
                # Best-effort row scan with openpyxl for milestones / dates
                try:
                    from openpyxl import load_workbook
                    wb = load_workbook(file_path, read_only=True, data_only=True)
                    sheet_summaries = []
                    for ws in wb.worksheets[:5]:
                        rows = list(ws.iter_rows(values_only=True, max_row=200))
                        sheet_summaries.append({
                            "name": ws.title,
                            "row_count": ws.max_row,
                            "col_count": ws.max_column,
                            "preview": [list(r)[:8] for r in rows[:5]],
                        })
                    xlsx_summary["sheets"] = sheet_summaries
                except Exception as exc:
                    # Previously silent — turned a corrupt/locked .xlsx into
                    # an empty "Schedule (Excel)" panel with no error signal.
                    # Now surface the failure so the UI can show why.
                    logger.warning(
                        "auto_pipeline: openpyxl load_workbook failed for %s: %s",
                        file_path, exc,
                    )
                    xlsx_summary["xlsx_error"] = f"Failed to read workbook: {exc}"
                downstream["schedule"] = xlsx_summary
                panels.append({
                    "type": "schedule",
                    "title": "Schedule (Excel)",
                    "data": xlsx_summary,
                })
                next_actions.append({
                    "action": "progress_tracker",
                    "label": "Track Progress",
                    "reason": "Excel schedule loaded — inspect sheets",
                })

        # Contract → process contract details
        if doc_type == "contract":
            try:
                contract_result = await self.process_contract(
                    {"file_path": file_path, "extracted_text": extracted_text}, {}
                )
                downstream["contract"] = contract_result
                panels.append({
                    "type": "contract",
                    "title": "Contract Analysis",
                    "data": contract_result
                })
                next_actions.append({
                    "action": "payment_certificate",
                    "label": "Issue Payment Certificate",
                    "reason": "Contract terms identified"
                })
            except Exception as exc:
                logger.exception("auto_pipeline: process_contract failed")
                pipeline_warnings.append({"panel": "contract", "error": str(exc)})

        # ── Chat context: structured text the user can follow up on ──────────
        chat_context_parts = [f"Document: {file_path.split('/')[-1]} (type: {doc_type})"]
        if quantities:
            chat_context_parts.append(f"Quantities found: {list(quantities.keys())[:10]}")
        if risks:
            chat_context_parts.append(f"Risks identified: {len(risks)}")
        if specs:
            chat_context_parts.append(f"Spec sections: {len(specs)}")
        for panel in panels:
            if panel["type"] == "cost_estimate":
                summary = panel.get("data", {})
                if summary.get("total_estimate"):
                    chat_context_parts.append(f"Total cost estimate: ${summary['total_estimate']:,.0f}")
        if extracted_text:
            chat_context_parts.append(f"\nExtracted text (first 3000 chars):\n{extracted_text[:3000]}")

        # Boundary validation: every panel passes through the typed contract
        # so a shape regression surfaces as a typed error_panel rather than
        # rendering as raw JSON in the UI. (See app/core/panels.py)
        from app.core.panels import validate_panel
        validated_panels = [validate_panel(p) for p in panels]

        return {
            "status": "success",
            "action": "auto_pipeline",
            "doc_type": doc_type,
            "panels": validated_panels,
            "downstream_actions_run": list(downstream.keys()),
            "next_actions": next_actions,
            "pipeline_warnings": pipeline_warnings,
            "chat_context": "\n".join(chat_context_parts),
            "raw_doc_result": doc_result,
        }
    async def qa_qc_inspection(self, input_data: Any, params: Dict) -> Dict:
        """Quality control inspection from photos or drawings"""
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        file_path = data.get("file_path") or p.get("file_path")
        inspection_type = p.get("type", "general")
    
        if not file_path:
            return {"status": "error", "error": "No inspection image provided"}
    
        image_block = self.get_dep("image")
    
        defect_prompts = {
            "concrete": "Detect cracks, honeycombing, cold joints, voids, spalling, discoloration",
            "masonry": "Check alignment, mortar joints, plumb, coursing, efflorescence, cracks",
            "steel": "Check welds, rust, alignment, bolt patterns, deformations",
            "finish": "Check paint coverage, drywall seams, flooring alignment, tile lippage",
            "general": "Detect construction defects, cracks, alignment issues, finish problems"
        }
    
        if image_block:
            try:
                analysis = await image_block.execute(
                    {"file_path": file_path},
                    {"prompt": defect_prompts.get(inspection_type, defect_prompts["general"])}
                )
                desc = analysis.get("result", {}).get("description", "")
            except Exception:
                desc = ""
        else:
            desc = ""
    
        defects = self._parse_defects(desc)
        compliance = self._check_compliance(defects, inspection_type)
    
        return {
            "status": "success",
            "inspection_type": inspection_type,
            "file": Path(file_path).name,
            "defects_found": len(defects),
            "defects": defects,
            "severity_score": self._calculate_severity(defects),
            "compliance_status": compliance["status"],
            "compliance_issues": compliance["issues"],
            "recommendations": self._generate_recommendations(defects, inspection_type),
            "pass_fail": "PASS" if not defects else "CONDITIONAL" if all(d["severity"] == "minor" for d in defects) else "FAIL"
        }
    _DEFECT_KEYWORDS = {
        "concrete": [
            ("crack", "Crack visible", "major"),
            ("honeycomb", "Honeycombing / segregation", "major"),
            ("spall", "Spalling / surface loss", "major"),
            ("efflorescence", "Efflorescence (moisture migration)", "minor"),
            ("scaling", "Surface scaling", "minor"),
            ("void", "Void / air pocket", "major"),
            ("rebar exposed", "Exposed reinforcement", "critical"),
            ("rebar corrosion", "Reinforcement corrosion", "critical"),
            ("delaminat", "Delamination", "major"),
            ("cold joint", "Cold joint", "minor"),
        ],
        "steel": [
            ("corrosion", "Corrosion", "major"),
            ("rust", "Surface rust", "minor"),
            ("deformation", "Deformation", "major"),
            ("misalignment", "Misalignment", "major"),
            ("missing bolt", "Missing bolt(s)", "critical"),
            ("loose bolt", "Loose bolt(s)", "major"),
            ("weld defect", "Weld defect", "critical"),
            ("paint failure", "Paint / coating failure", "minor"),
        ],
        "masonry": [
            ("crack", "Cracking", "major"),
            ("mortar loss", "Mortar joint loss", "minor"),
            ("displacement", "Brick / block displacement", "major"),
            ("efflorescence", "Efflorescence", "minor"),
        ],
        "finishes": [
            ("paint peeling", "Paint peeling", "minor"),
            ("tile crack", "Tile cracking", "minor"),
            ("water stain", "Water stain", "minor"),
            ("mould", "Mould / mildew", "major"),
        ],
    }
    async def _compare_photo_to_bim(self, photo_path: str, bim_file: str, location: str) -> Dict:
        """Visual SLAM + BIM comparison"""
        image_block = self.get_dep("image")
    
        if image_block:
            try:
                photo_analysis = await image_block.execute(
                    {"file_path": photo_path},
                    {"prompt": f"Identify construction elements at {location}: walls, columns, beams, slabs, openings, MEP rough-ins"}
                )
                detected = photo_analysis.get("result", {}).get("objects", [])
            except Exception:
                detected = []
        else:
            detected = []
    
        expected_elements = await self._query_bim_location(bim_file, location)
    
        matched = []
        missing = []
        for expected in expected_elements:
            match = any(self._element_similarity(expected, d) > 0.6 for d in detected)
            if match:
                matched.append(expected)
            else:
                missing.append(expected)
    
        return {
            "location": location,
            "photo": Path(photo_path).name,
            "match_confidence": len(matched) / len(expected_elements) if expected_elements else 0,
            "elements_detected": len(detected),
            "elements_expected": len(expected_elements),
            "matched": matched,
            "missing": missing,
            "deviations": self._find_deviations(detected, expected_elements)
        }
    async def _query_bim_location(self, bim_file: str, location: str) -> List[Dict]:
        """Query IFC for elements at a specific location via the bim_extractor.

        Always returns a list (possibly empty) to keep the contract honest
        with the `-> List[Dict]` annotation. The caller `_compare_photo_to_bim`
        iterates this and divides by `len(...)` — a dict return would have
        iterated key names and miscounted match confidence.

        The most recent error message (if any) is stashed on
        `self._last_bim_query_error` so callers that care can surface it
        without polluting the return value.
        """
        self._last_bim_query_error: Optional[str] = None
        block = self._resolve_block("bim_extractor")
        if block is None:
            self._last_bim_query_error = "BIM extractor not configured for spatial queries"
            return []
        try:
            result = await block.process(
                {"file_path": bim_file},
                {"action": "query_location", "location": location},
            )
        except Exception as exc:
            self._last_bim_query_error = f"BIM extractor query failed: {exc}"
            return []
        if not isinstance(result, dict) or result.get("status") != "success":
            self._last_bim_query_error = (
                (result or {}).get("error") if isinstance(result, dict)
                else "BIM extractor returned malformed response"
            )
            return []
        elements = result.get("elements")
        return elements if isinstance(elements, list) else []
