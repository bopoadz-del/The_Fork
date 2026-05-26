"""Shared dataclasses for construction-domain code.

Both `app/containers/construction.py` and `app/blocks/construction_v2.py`
historically defined `Measurement`, `SpecItem`, and `RiskItem` independently
— identical fields, but two source-of-truth copies. Any future field
change to one would diverge silently.

This module is now the single definition; both files import from here.
"""

from dataclasses import dataclass


@dataclass
class Measurement:
    """Numeric quantity extracted from a drawing, BOQ, or spec.

    `type` is the kind of measurement (e.g. 'area', 'length', 'volume', 'count').
    `confidence` is 0-1; producers should set it honestly so downstream filters
    can drop low-confidence extractions.
    """
    value: float
    unit: str
    type: str
    raw_text: str
    confidence: float
    context: str


@dataclass
class SpecItem:
    """A single piece of structured information extracted from a specification.

    `category` groups items (e.g. 'concrete_grade', 'rebar_grade', 'standard').
    `section` is the spec section reference where the item was found, when known.
    """
    category: str
    key: str
    value: str
    section: str
    confidence: float


@dataclass
class RiskItem:
    """A construction-project risk entry.

    `probability` and `impact` are typically qualitative strings ('low',
    'medium', 'high'). `source` is the document or block that surfaced
    the risk.
    """
    id: str
    category: str
    description: str
    probability: str
    impact: str
    mitigation: str
    source: str
