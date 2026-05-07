"""Construction Block v2 - TypedBlock implementation

This is the v2 implementation that:
- Extends TypedBlock instead of UniversalContainer
- Accepts TextContent input (from pdf_v2, ocr_v2, etc.)
- Outputs ConstructionAnalysis type
- Has a single process() entry point instead of action routing
- Internal methods are private (_ prefixed)
"""

import re
import json
import os
import math
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

from app.core.typed_block import TypedBlock
from app.core.schema_registry import TextContent, ConstructionAnalysis


@dataclass
class Measurement:
    value: float
    unit: str
    type: str
    raw_text: str
    confidence: float
    context: str


@dataclass
class SpecItem:
    category: str
    key: str
    value: str
    section: str
    confidence: float


@dataclass
class RiskItem:
    id: str
    category: str
    description: str
    probability: str
    impact: str
    mitigation: str
    source: str


class ConstructionBlockV2(TypedBlock):
    """
    Construction Block v2 - TypedBlock implementation for AEC analysis.
    
    Input: TextContent (extracted document text)
    Output: ConstructionAnalysis (measurements, quantities, materials)
    
    This replaces the v1 UniversalContainer with a clean, typed interface.
    """
    
    name = "construction_v2"
    version = "2.0"
    description = "Construction document analysis with typed input/output"
    layer = 3
    tags = ["domain", "construction", "aec", "v2"]
    requires = []
    
    default_config = {
        "confidence_threshold": 0.85,
        "default_trade": "concrete",
        "extract_measurements": True,
        "extract_quantities": True,
        "extract_materials": True
    }
    
    # Input: TextContent from pdf_v2, ocr_v2, etc.
    input_schema = TextContent
    
    # Output: ConstructionAnalysis
    output_schema = ConstructionAnalysis
    
    # Type declarations for orchestrator
    accepted_input_types = ["TextContent", "PDFContent"]
    produced_output_types = ["ConstructionAnalysis"]
    
    ui_schema = {
        "input": {
            "type": "text",
            "placeholder": "Paste construction document text or chain from PDF/OCR...",
            "multiline": True
        },
        "output": {
            "type": "table",
            "fields": [
                {"name": "concrete_volume_m3", "type": "number", "unit": "m³", "label": "Concrete"},
                {"name": "steel_weight_kg", "type": "number", "unit": "kg", "label": "Steel"},
                {"name": "floor_area_m2", "type": "number", "unit": "m²", "label": "Floor Area"},
                {"name": "confidence", "type": "percentage", "label": "Confidence"}
            ]
        },
        "quick_actions": [
            {"icon": "📐", "label": "Measure Drawing", "prompt": "Extract all measurements from this drawing"},
            {"icon": "📊", "label": "Calculate Quantities", "prompt": "Calculate BOQ from this drawing"},
            {"icon": "⚠️", "label": "Check Compliance", "prompt": "Check this against Saudi building codes"},
            {"icon": "🌱", "label": "Carbon Estimate", "prompt": "Estimate embodied carbon for this project"}
        ]
    }
    
    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """
        Main entry point - analyze construction document text.
        
        Input: TextContent dict (or string for backward compatibility)
        Output: ConstructionAnalysis dict
        """
        params = params or {}
        
        # Extract text from TextContent format (or plain string)
        text = self._extract_text(input_data)
        if not text:
            return self._empty_analysis("No text provided")
        
        # Determine analysis type from params or auto-detect
        analysis_type = params.get("analysis_type", self._detect_document_type(text))
        
        # Run analysis based on type
        if analysis_type == "drawing":
            return await self._analyze_drawing(text, params)
        elif analysis_type == "specification":
            return await self._analyze_specification(text, params)
        elif analysis_type == "contract":
            return await self._analyze_contract(text, params)
        elif analysis_type == "schedule":
            return await self._analyze_schedule(text, params)
        else:
            # Generic analysis
            return await self._analyze_generic(text, params)
    
    # ─────────────────────────────────────────────────────────────────
    # TEXT EXTRACTION (handles TextContent or plain string)
    # ─────────────────────────────────────────────────────────────────
    
    def _extract_text(self, input_data: Any) -> str:
        """Extract text from TextContent or plain string."""
        if isinstance(input_data, str):
            return input_data
        elif isinstance(input_data, dict):
            # TextContent format
            if "text" in input_data:
                return input_data["text"]
            # Legacy format
            return input_data.get("content", "")
        return ""
    
    def _detect_document_type(self, text: str) -> str:
        """Auto-detect document type from content."""
        text_lower = text[:5000].lower()
        
        # Check for drawing indicators
        if any(kw in text_lower for kw in ["drawing", "plan", "elevation", "section", "scale", "dimension"]):
            return "drawing"
        
        # Check for spec indicators
        if any(kw in text_lower for kw in ["specification", "masterformat", "section", "division", "material"]):
            return "specification"
        
        # Check for contract indicators
        if any(kw in text_lower for kw in ["contract", "agreement", "clause", "shall", "party", "terms"]):
            return "contract"
        
        # Check for schedule indicators
        if any(kw in text_lower for kw in ["schedule", "gantt", "milestone", "activity", "duration", "primavera"]):
            return "schedule"
        
        return "generic"
    
    # ─────────────────────────────────────────────────────────────────
    # ANALYSIS METHODS (private - internal use only)
    # ─────────────────────────────────────────────────────────────────
    
    async def _analyze_drawing(self, text: str, params: Dict) -> Dict:
        """Analyze construction drawing text."""
        measurements = self._extract_measurements(text)
        quantities = self._calculate_quantities(measurements)
        materials = self._extract_materials(text)
        
        return {
            "measurements": measurements,
            "quantities": quantities,
            "materials": materials,
            "confidence": 0.85,
            "raw_text": text[:2000] if params.get("include_raw") else "",
            "metadata": {
                "analysis_type": "drawing",
                "measurement_count": len(measurements),
                "material_count": len(materials),
                "extracted_at": self._timestamp()
            }
        }
    
    async def _analyze_specification(self, text: str, params: Dict) -> Dict:
        """Analyze specification document text."""
        materials = self._extract_materials(text)
        methods = self._extract_methods(text)
        qa_qc = self._extract_qaqc(text)
        
        return {
            "measurements": [],
            "quantities": {},
            "materials": materials + methods + qa_qc,
            "confidence": 0.80,
            "raw_text": text[:2000] if params.get("include_raw") else "",
            "metadata": {
                "analysis_type": "specification",
                "material_count": len(materials),
                "extracted_at": self._timestamp()
            }
        }
    
    async def _analyze_contract(self, text: str, params: Dict) -> Dict:
        """Analyze contract document text."""
        clauses = self._extract_clauses(text)
        obligations = self._extract_obligations(text)
        financial = self._extract_financial_terms(text)
        
        return {
            "measurements": [],
            "quantities": financial,
            "materials": obligations,
            "confidence": 0.75,
            "raw_text": text[:2000] if params.get("include_raw") else "",
            "metadata": {
                "analysis_type": "contract",
                "clauses_found": len(clauses),
                "extracted_at": self._timestamp()
            }
        }
    
    async def _analyze_schedule(self, text: str, params: Dict) -> Dict:
        """Analyze schedule document text."""
        activities = self._extract_activities(text)
        milestones = self._extract_milestones_from_text(text)
        
        return {
            "measurements": activities,
            "quantities": {"activities": len(activities), "milestones": len(milestones)},
            "materials": milestones,
            "confidence": 0.70,
            "raw_text": text[:2000] if params.get("include_raw") else "",
            "metadata": {
                "analysis_type": "schedule",
                "activity_count": len(activities),
                "extracted_at": self._timestamp()
            }
        }
    
    async def _analyze_generic(self, text: str, params: Dict) -> Dict:
        """Generic analysis for unknown document types."""
        measurements = self._extract_measurements(text)
        materials = self._extract_materials(text)
        
        return {
            "measurements": measurements,
            "quantities": self._calculate_quantities(measurements),
            "materials": materials,
            "confidence": 0.60,
            "raw_text": text[:2000] if params.get("include_raw") else "",
            "metadata": {
                "analysis_type": "generic",
                "extracted_at": self._timestamp()
            }
        }
    
    # ─────────────────────────────────────────────────────────────────
    # EXTRACTION HELPERS (private)
    # ─────────────────────────────────────────────────────────────────
    
    def _extract_measurements(self, text: str) -> List[Dict]:
        """Extract measurements from text."""
        measurements = []
        
        # Dimension pattern: 5.5m x 3.2m
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
        
        # Quantity pattern: 5 no. Concrete Column
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
    
    def _calculate_quantities(self, measurements: List[Dict]) -> Dict:
        """Calculate construction quantities from measurements."""
        total_area = sum(m.get("value", 0) for m in measurements if m.get("type") == "dimension")
        
        # Standard estimates
        concrete_volume = total_area * 0.15  # 150mm typical slab
        steel_weight = concrete_volume * 120  # 120 kg/m³
        rebar_length = concrete_volume * 50  # 50m per m³
        
        return {
            "floor_area_m2": round(total_area, 2),
            "concrete_volume_m3": round(concrete_volume, 2),
            "steel_weight_kg": round(steel_weight, 2),
            "rebar_length_m": round(rebar_length, 2)
        }
    
    def _extract_materials(self, text: str) -> List[Dict]:
        """Extract material references from text."""
        materials = []
        material_keywords = [
            "concrete", "steel", "rebar", "brick", "block", "glass", 
            "aluminum", "timber", "insulation", "membrane", "tile",
            "gypsum", "cement", "aggregate", "sand"
        ]
        
        text_lower = text.lower()
        for kw in material_keywords:
            if kw in text_lower:
                # Find context
                idx = text_lower.find(kw)
                context = text[max(0, idx-50):idx+50]
                materials.append({
                    "material": kw,
                    "context": context,
                    "confidence": 0.9
                })
        
        return materials[:20]
    
    def _extract_methods(self, text: str) -> List[Dict]:
        """Extract construction methods from text."""
        methods = []
        method_keywords = ["pour", "cast", "place", "install", "erect", "frame"]
        
        text_lower = text.lower()
        for kw in method_keywords:
            if kw in text_lower:
                methods.append({
                    "type": "method",
                    "value": kw,
                    "confidence": 0.8
                })
        
        return methods
    
    def _extract_qaqc(self, text: str) -> List[Dict]:
        """Extract QA/QC requirements from text."""
        qa = []
        if re.search(r'\binspection\b|\bwitness\b|\bhold point\b', text, re.IGNORECASE):
            qa.append({
                "type": "qa_qc",
                "value": "Inspection/witness requirements found",
                "confidence": 0.85
            })
        return qa
    
    def _extract_clauses(self, text: str) -> List[Dict]:
        """Extract contract clauses from text."""
        clauses = []
        clause_patterns = {
            "payment_terms": r'(?:payment|pay|invoice)[\s\w]{0,50}(?:term|schedule|milestone)',
            "liquidated_damages": r'(?:liquidated damages|ld|delay damages)',
            "retention": r'(?:retention|retainage)',
            "termination": r'(?:terminat|cancel|end)[\s\w]{0,100}(?:notice|for cause)',
        }
        
        for clause_name, pattern in clause_patterns.items():
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if matches:
                clauses.append({
                    "type": clause_name,
                    "found": True,
                    "count": len(matches),
                    "example": matches[0].group(0)[:200]
                })
        
        return clauses
    
    def _extract_obligations(self, text: str) -> List[Dict]:
        """Extract contractual obligations from text."""
        obligations = []
        obligation_patterns = [
            (r'(?:contractor|builder)[\s\w]{0,50}(?:shall|must|will)[\s\w]{0,100}(?:\.)', "contractor_obligation"),
            (r'(?:employer|owner|client)[\s\w]{0,50}(?:shall|must|will)[\s\w]{0,100}(?:\.)', "employer_obligation"),
        ]
        
        for pattern, obl_type in obligation_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                obligations.append({
                    "type": obl_type,
                    "text": match.group(0),
                    "confidence": 0.8
                })
        
        return obligations[:10]
    
    def _extract_financial_terms(self, text: str) -> Dict:
        """Extract financial terms from contract text."""
        terms = {}
        
        value_match = re.search(r'(?:contract (?:value|sum|price|amount)|total)[\s:]*[$\u20ac£]?[\s]*(\d[\d,\.]*)', text, re.IGNORECASE)
        if value_match:
            terms["contract_value"] = value_match.group(1)
        
        retention_match = re.search(r'(?:retention|retainage)[\s\w]{0,30}(\d+)%', text, re.IGNORECASE)
        if retention_match:
            terms["retention"] = f"{retention_match.group(1)}%"
        
        return terms
    
    def _extract_activities(self, text: str) -> List[Dict]:
        """Extract schedule activities from text."""
        activities = []
        # Simple pattern for activity lines
        activity_pattern = r'(?:activity|task)[\s\w]{0,20}(\d+)[\s\w]{0,50}(\d+)\s*(?:day|week)'
        
        for match in re.finditer(activity_pattern, text, re.IGNORECASE):
            activities.append({
                "id": match.group(1),
                "duration": match.group(2),
                "type": "activity"
            })
        
        return activities
    
    def _extract_milestones_from_text(self, text: str) -> List[Dict]:
        """Extract milestone references from text."""
        milestones = []
        milestone_keywords = ["milestone", "substantial completion", "practical completion", "handover"]
        
        for kw in milestone_keywords:
            if kw in text.lower():
                milestones.append({
                    "type": "milestone",
                    "name": kw,
                    "confidence": 0.75
                })
        
        return milestones
    
    def _empty_analysis(self, message: str) -> Dict:
        """Return empty analysis with error message."""
        return {
            "measurements": [],
            "quantities": {},
            "materials": [],
            "confidence": 0,
            "raw_text": "",
            "metadata": {
                "error": message,
                "extracted_at": self._timestamp()
            }
        }
    
    def _timestamp(self) -> str:
        """Get current ISO timestamp."""
        return datetime.now(timezone.utc).isoformat()
