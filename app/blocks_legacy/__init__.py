"""Platform Blocks - Self-contained (cloned from Block Store).

These are LOCAL copies of blocks the platform uses.
The Block Store (blocks/) is separate - for publishing/discovery.
"""

# Core AI Blocks (cloned from Block Store)
from .pdf import PDFBlock
from .ocr import OCRBlock
from .chat import ChatBlock
from .voice import VoiceBlock
from .vector_search import VectorSearchBlock
from .image import ImageBlock
from .translate import TranslateBlock
from .code import CodeBlock
from .web import WebBlock
from .search import SearchBlock
from .zvec import ZvecBlock

# Drive Blocks
from .google_drive import GoogleDriveBlock
from .onedrive import OneDriveBlock
from .local_drive import LocalDriveBlock
from .android_drive import AndroidDriveBlock

# Containers
from app.containers import (
    StoreContainer, SecurityContainer, AICoreContainer, ConstructionContainer,
    MedicalContainer, LegalContainer, FinanceContainer
)

__all__ = [
    # AI Blocks
    "PDFBlock", "OCRBlock", "ChatBlock", "VoiceBlock", "VectorSearchBlock",
    "ImageBlock", "TranslateBlock", "CodeBlock", "WebBlock", "SearchBlock", "ZvecBlock",
    # Drive Blocks
    "GoogleDriveBlock", "OneDriveBlock", "LocalDriveBlock", "AndroidDriveBlock",
    # Containers
    "StoreContainer", "SecurityContainer", "AICoreContainer", "ConstructionContainer",
    "MedicalContainer", "LegalContainer", "FinanceContainer",
    # Registry
    "BLOCK_REGISTRY", "get_block", "get_all_blocks"
]

# Platform's local block registry (15 core blocks)
BLOCK_REGISTRY = {
    # Core blocks
    "pdf": PDFBlock,
    "ocr": OCRBlock,
    "chat": ChatBlock,
    "voice": VoiceBlock,
    "vector_search": VectorSearchBlock,
    "image": ImageBlock,
    "translate": TranslateBlock,
    "code": CodeBlock,
    "web": WebBlock,
    "search": SearchBlock,
    "zvec": ZvecBlock,
    "google_drive": GoogleDriveBlock,
    "onedrive": OneDriveBlock,
    "local_drive": LocalDriveBlock,
    "android_drive": AndroidDriveBlock,
    # Containers
    "store": StoreContainer,
    "security": SecurityContainer,
    "ai_core": AICoreContainer,
    "construction": ConstructionContainer,
    "medical": MedicalContainer,
    "legal": LegalContainer,
    "finance": FinanceContainer,
}

def register_block(name: str, block_class):
    """Register a block class."""
    BLOCK_REGISTRY[name] = block_class

def get_block(name: str):
    """Get a block class by name."""
    return BLOCK_REGISTRY.get(name)

def get_all_blocks():
    """Get all registered blocks."""
    return BLOCK_REGISTRY

# Note: Block Store (blocks/) is separate for publishing new blocks
# Platform only uses its local cloned blocks above
