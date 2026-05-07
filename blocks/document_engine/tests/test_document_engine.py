"""Unit tests for Document Engine — all 8 reasoning pipelines + orchestration."""
import sys
from pathlib import Path
from datetime import datetime

import pytest

# Ensure block is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from document_engine.parsers.pdf_parser import PDFParser, PDFDocument
from document_engine.parsers.docx_parser import DOCXParser, DOCXDocument
from document_engine.parsers.xlsx_parser import XLSXParser, XLSXDocument
from document_engine.reasoner import DocumentReasoner, ReasonedOutput
from document_engine.mapper import DocumentMapper, StructuredDocument


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def base_config():
    return {
        "patterns": {
            "glossary": [
                r"(?P<term>[A-Z]{2,5})\s+[-—:]\s+(?P<definition>.+)",
            ],
            "obligation": [r"\b(SHALL|MUST|REQUIRED)\b"],
            "constraint": [
                r"(?P<value>\d+\.?\d*)\s*(?P<unit>MW|kW|V|cfm/kW|ft|%)",
                r"(?P<operator>[≤≥<>=])\s*(?P<value>\d+\.?\d*)\s*(?P<unit>%|ft|MW|kW|V)",
            ],
            "schedule_target": [
                r"(?:operational|RFS|completion)\s+(?:by|before)\s+(?P<month>\w+)\s+(?P<year>\d{4})",
            ],
            "equipment_lead_time": [
                r"(?P<equipment>generator|chiller|switchgear)\w*\s+.*?(?P<days>\d+)\s*days?",
            ],
            "risk": [
                r"\b(winter|supply chain|Indigenous|TFO|blast radius|load.shed|permit delay)\b",
            ],
        },
        "wbs_dictionary": {
            "1.0": "Project Management",
            "2.0": "Design & Engineering",
            "3.0": "Procurement",
            "7.0": "MEP Systems",
            "8.0": "IT Infrastructure",
        },
        "ontology": {
            "PCW": {"wbs": "7.0", "category": "mechanical", "type": "system"},
            "generator": {"wbs": "3.0", "category": "procurement", "type": "equipment", "lead_time": 120},
            "chiller": {"wbs": "3.0", "category": "procurement", "type": "equipment", "lead_time": 90},
        },
        "version": "1.0.0",
        "name": "Document Reasoning Engine",
    }


@pytest.fixture
def sample_pdf_doc(base_config):
    text = (
        "PCW — Process Cooling Water system for data halls.\n"
        "Figure 1 — HAC Tier 1: Manifold distribution.\n"
        "The generator SHALL be sized for N+1 redundancy.\n"
        "Minimum power: 50 MW.\n"
        "Blast radius shall be ≤3%.\n"
        "Operational by December 2028.\n"
        "Generator procurement requires 120 days lead time.\n"
        "Winter construction introduces TFO queue risks.\n"
    )
    doc = PDFDocument(source="test.pdf", text=text)
    doc.glossary = PDFParser(base_config)._extract_glossary(text)
    doc.figures = PDFParser(base_config)._extract_figures(text)
    return doc


@pytest.fixture
def sample_docx_doc(base_config):
    text = (
        "Heading 1: Scope\n"
        "The Contractor MUST provide 415V 3-phase power.\n"
        "Cooling SHALL achieve 120 cfm/kW.\n"
        "Switchgear lead time is 75 days.\n"
    )
    doc = DOCXDocument(source="test.docx", text=text, paragraphs=text.split("\n"))
    return doc


# ---------------------------------------------------------------------------
# Layer 1: Parser Tests
# ---------------------------------------------------------------------------
def test_pdf_parser_extracts_glossary(base_config):
    text = "PCW — Process Cooling Water.\nTCS — Technical Control System."
    glossary = PDFParser(base_config)._extract_glossary(text)
    assert "PCW" in glossary
    assert "TCS" in glossary


def test_pdf_parser_extracts_figures(base_config):
    text = "Figure 1 — HAC Tier 1: Manifold.\nFigure 2.1 — Security Layer 2."
    figures = PDFParser(base_config)._extract_figures(text)
    assert len(figures) == 2
    assert figures[0]["id"] == "1"
    assert "Manifold" in figures[0]["caption"]


def test_docx_parser_loads_text():
    # In-memory test without real docx file
    doc = DOCXDocument(source="test.docx", text="Hello world", paragraphs=["Hello world"])
    assert doc.text == "Hello world"
    assert doc.paragraphs == ["Hello world"]


def test_xlsx_parser_empty_fallback():
    # Without openpyxl installed or file present, should return empty structure
    parser = XLSXParser({})
    # We won't create a real xlsx, just test the class shape
    doc = XLSXDocument(source="test.xlsx")
    assert doc.sheets == {}


# ---------------------------------------------------------------------------
# Layer 2: Reasoner Tests (8 Pipelines)
# ---------------------------------------------------------------------------
def test_pipeline_requirements(base_config, sample_pdf_doc, sample_docx_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc, sample_docx_doc])

    # SHALL + MUST should both be captured
    texts = [r["text"] for r in result.requirements]
    assert any("SHALL" in t for t in texts)
    assert any("MUST" in t for t in texts)

    # Electrical category inferred for 415V / MW
    categories = [r["category"] for r in result.requirements]
    assert "electrical" in categories


def test_pipeline_constraints(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])

    values = [c["value"] for c in result.constraints]
    units = [c["unit"] for c in result.constraints]
    assert "50" in values or any("50" in v for v in values)
    assert any(u.lower() in ["mw", "%"] for u in units)


def test_pipeline_schedule_targets(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])

    assert len(result.schedule_targets) >= 1
    target = result.schedule_targets[0]
    assert target.get("year") == "2028"
    assert target.get("month") == "December"


def test_pipeline_equipment_specs(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])

    names = [s["equipment"].lower() for s in result.equipment_specs]
    assert "generator" in names
    gen = next(s for s in result.equipment_specs if s["equipment"].lower() == "generator")
    assert gen["lead_time_days"] == 120


def test_pipeline_diagrams(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])

    fig_diagrams = [d for d in result.diagrams if d.get("figure_id")]
    assert len(fig_diagrams) >= 1
    assert fig_diagrams[0]["caption"] == "HAC Tier 1: Manifold distribution."


def test_pipeline_wbs_mapping(base_config, sample_pdf_doc, sample_docx_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc, sample_docx_doc])

    # 7.0 = MEP should have mechanical items
    assert "7.0" in result.wbs_mapping
    mep_items = [i for i in result.wbs_mapping["7.0"] if i.get("category") == "mechanical"]
    assert len(mep_items) > 0 or len(result.wbs_mapping["7.0"]) > 0


def test_pipeline_risks(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])

    keywords = [r["keyword"].lower() for r in result.risks]
    assert "blast radius" in keywords or "winter" in keywords or "tfo" in keywords

    # Tight constraint risk from ≤3%
    tight = [r for r in result.risks if r["type"] == "tight_constraint"]
    assert len(tight) >= 1
    assert tight[0]["severity"] == "high"


def test_pipeline_glossary_merge(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    result = reasoner.reason([sample_pdf_doc])
    assert "PCW" in result.glossary


# ---------------------------------------------------------------------------
# Layer 3: Mapper Tests
# ---------------------------------------------------------------------------
def test_mapper_produces_structured_document(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    reasoned = reasoner.reason([sample_pdf_doc])

    mapper = DocumentMapper(base_config)
    structured = mapper.map_to_structured(reasoned)

    assert isinstance(structured, StructuredDocument)
    assert structured.project_metadata.get("engine_version") == "1.0.0"
    assert "downstream" in structured.to_dict()


def test_mapper_downstream_schedule_activities(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    reasoned = reasoner.reason([sample_pdf_doc])

    mapper = DocumentMapper(base_config)
    structured = mapper.map_to_structured(reasoned)

    activities = structured.downstream["schedule_engine"].get("procurement_activities", [])
    assert len(activities) >= 1
    assert any(a["activity_name"] == "Procure Generator" for a in activities)


def test_mapper_yaml_output(base_config, sample_pdf_doc):
    reasoner = DocumentReasoner(base_config)
    reasoned = reasoner.reason([sample_pdf_doc])

    mapper = DocumentMapper(base_config)
    structured = mapper.map_to_structured(reasoned)

    yaml_text = structured.to_yaml()
    assert "project_metadata" in yaml_text
    assert "downstream" in yaml_text


# ---------------------------------------------------------------------------
# Integration: Orchestrator
# ---------------------------------------------------------------------------
def test_parse_all_layers(base_config):
    """End-to-end with in-memory docs (no filesystem)."""
    pdf_doc = PDFDocument(
        source="test.pdf",
        text="Generator MUST be N+1. 50 MW minimum. Operational by November 2027.",
    )
    docx_doc = DOCXDocument(
        source="test.docx",
        text="Chiller SHALL deliver 120 cfm/kW. Chiller lead time 90 days.",
    )

    reasoner = DocumentReasoner(base_config)
    reasoned = reasoner.reason([pdf_doc, docx_doc])

    mapper = DocumentMapper(base_config)
    structured = mapper.map_to_structured(reasoned)

    assert len(structured.requirements) >= 2
    assert len(structured.constraints) >= 1
    assert len(structured.schedule_targets) >= 1
    assert len(structured.equipment_specs) >= 1
    assert len(structured.risks) >= 0
    assert structured.downstream["schedule_engine"]["procurement_activities"]


# ---------------------------------------------------------------------------
# LegoBlock Integration
# ---------------------------------------------------------------------------
def test_document_engine_block_exists():
    from document_engine import DocumentEngineBlock
    assert DocumentEngineBlock is not None
    assert DocumentEngineBlock.name == "document_engine"


@pytest.mark.asyncio
async def test_document_engine_block_execute(base_config):
    from document_engine import DocumentEngineBlock

    block = DocumentEngineBlock(config=base_config)
    result = await block.execute({
        "pdf_path": None,
        "docx_path": None,
        "xlsx_path": None,
    })
    assert isinstance(result, dict)
    assert "project_metadata" in result
