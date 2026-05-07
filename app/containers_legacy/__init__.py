"""Platform Containers - Layer 3 Domain Adapters (Domain Adapter Protocol)."""

from .store import StoreContainer
from .security import SecurityContainer
from .ai_core import AICoreContainer
from .construction import ConstructionContainer
from .medical import MedicalContainer
from .legal import LegalContainer
from .finance import FinanceContainer

__all__ = [
    "StoreContainer",
    "SecurityContainer", 
    "AICoreContainer",
    "ConstructionContainer",
    "MedicalContainer",
    "LegalContainer",
    "FinanceContainer",
]
