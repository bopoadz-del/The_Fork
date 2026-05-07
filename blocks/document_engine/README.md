# Cerebrum-Blocks / Document Engine

Modular document reasoning engine for construction project intelligence.
Extracts glossary terms, requirements, constraints, schedule targets, equipment specs,
and WBS mappings from technical documents (BODs, RFPs, BOQs, specs).

## Architecture (Lego Blocks)

| Block | File | Role |
|-------|------|------|
| Config | `config.yaml` | Supported formats, regex patterns, WBS dictionary, ontology |
| PDF Parser | `parsers/pdf_parser.py` | Text, tables, glossary, figure extraction from PDF |
| DOCX Parser | `parsers/docx_parser.py` | Headings, paragraphs, lists, tables from Word |
| XLSX Parser | `parsers/xlsx_parser.py` | Sheets, headers, schedule template fields from Excel |
| Reasoner | `reasoner.py` | 8 semantic reasoning pipelines (the brain) |
| Mapper | `mapper.py` | Transforms reasoned output to structured YAML/JSON |
| Orchestrator | `main.py` | CLI entry point — wires Parse → Reason → Map |

## The 3-Layer Pipeline

```
Input Files
    │
    ▼
[Layer 1] Parse ──► PDFDocument / DOCXDocument / XLSXDocument
    │
    ▼
[Layer 2] Reason ──► ReasonedOutput (entities, constraints, targets, risks)
    │
    ▼
[Layer 3] Map ──► StructuredDocument (YAML/JSON, downstream-consumable)
```

### Layer 1: Parse (Syntactic Extraction)
- Format detection → route to parser
- Text normalization → strip headers/footers, collapse whitespace
- Chunk segmentation → split by headings, pages, table boundaries
- Annotation tagging → mark each chunk: paragraph, table, list, glossary_entry, diagram_caption

### Layer 2: Reason (Semantic Extraction)
Four pattern engines run in parallel over every chunk:

| Pipeline | What It Finds |
|----------|---------------|
| Glossary Extraction | PCW, TCS, CDU, ANR, HAC |
| Requirement Mapping | SHALL/MUST/REQUIRED + domain categorization |
| Constraint Extraction | ≤3%, 50MW, 415V, 120 cfm/kW |
| Schedule Targets | EOY 2027, operational by Dec 2028 |
| Equipment Specs | Generator 120d, Chiller 90d, Switchgear 75d |
| Diagram Interpretation | HAC Tier 1-4, Security Layer 2-5 |
| WBS Mapping | Keyword matching reqs → WBS codes |
| Risk Identification | Winter, TFO queue, supply chain, Indigenous |

### Layer 3: Map (Pragmatic Structuring)
- Ontology alignment → map entities to domain model
- Cardinality inference → normalize totals
- Temporal ordering → infer dependency chains
- Risk surfacing → flag external dependencies

## Quick Start

```bash
pip install -r requirements.txt
python blocks/document_engine/main.py \
  --pdf "Performance Basis of Design.pdf" \
  --docx "Request for Proposals.docx" \
  --xlsx "Appendix B.xlsx" \
  --output analysis.yaml \
  --verbose
```

## Output

Structured YAML consumed directly by downstream blocks:
- `schedule_engine` → equipment lead times become procurement activities
- `cost_engine` → WBS mappings become cost code buckets
- `risk_engine` → identified risks become risk register entries

## LegoBlock Integration

```python
from blocks.document_engine import DocumentEngineBlock

block = DocumentEngineBlock(config={...})
result = await block.execute({
    "pdf_path": "BOD.pdf",
    "docx_path": "RFP.docx",
    "xlsx_path": "Appendix.xlsx"
})
```
