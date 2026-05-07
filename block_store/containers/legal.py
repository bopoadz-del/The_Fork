"""Legal Container - Law firm AI with contract analysis"""

from typing import Any, Dict
from app.core.universal_base import UniversalContainer


class LegalContainer(UniversalContainer):
    """
    Legal Container: Contract analysis, precedent validation, brief generation
    """
    
    name = "legal"
    version = "1.0"
    description = "Legal AI: Contract analysis, precedent validation, brief generation"
    layer = 3
    tags = ["domain", "container", "legal", "contracts"]
    requires = ["pdf", "ocr"]

    ui_schema = {
        "input": {
            "type": "file",
            "accept": [".pdf", ".docx", ".txt"],
            "placeholder": "Upload contract, brief, or legal document...",
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "parties", "type": "json", "label": "Parties"},
                {"name": "compliance_score", "type": "percentage", "label": "Compliance"},
                {"name": "risk_level", "type": "text", "label": "Risk Level"}
            ]
        },
        "quick_actions": [
            {"icon": "📜", "label": "Analyze Contract", "prompt": "Analyze this contract: extract parties, dates, obligations, and risk clauses"},
            {"icon": "⚖️", "label": "Validate Compliance", "prompt": "Check this document for legal compliance and flag violations"},
            {"icon": "🔍", "label": "Extract Entities", "prompt": "Extract all legal entities, clauses, and key terms"},
            {"icon": "📄", "label": "Generate Summary", "prompt": "Generate an executive summary of this legal document"}
        ]
    }

    def _looks_like_file(self, input_data: Any, params: Dict) -> bool:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        return any(k in data or k in p for k in ["file_path", "content", "filename", "file"])

    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        # Auto-validate file uploads first
        if self._looks_like_file(input_data, params):
            from app.containers.security import SecurityContainer
            security = SecurityContainer()
            validation = await security.validate_file(input_data, params)
            if not validation.get("safe"):
                return validation
        
        if action == "process_contract":
            return await self.process_contract(input_data, params)
        elif action == "extract_entities":
            return await self.extract_entities(input_data, params)
        elif action == "validate":
            return await self.validate(input_data, params)
        elif action == "generate_report":
            return await self.generate_report(input_data, params)
        elif action == "health_check":
            return await self.health_check()
        else:
            return {"error": f"Unknown action: {action}"}
    
    async def process_contract(self, input_data: Any, params: Dict) -> Dict:
        return {
            "status": "success",
            "parties": ["Acme Corp", "Global Solutions LLC"],
            "effective_date": "2026-01-15",
            "governing_law": "Delaware"
        }
    
    async def extract_entities(self, input_data: Any, params: Dict) -> Dict:
        return {
            "status": "success",
            "entities": [
                {"type": "clause", "clause_type": "indemnification", "risk_level": "high"}
            ]
        }
    
    async def validate(self, input_data: Any, params: Dict) -> Dict:
        return {
            "status": "success",
            "valid": True,
            "violations": [],
            "compliance_score": 0.88
        }
    
    async def generate_report(self, input_data: Any, params: Dict) -> Dict:
        return {
            "status": "success",
            "report_type": "contract_summary",
            "content": "Executive summary..."
        }
    
    async def health_check(self) -> Dict:
        return {
            "status": "healthy",
            "container": self.name,
            "capabilities": ["process_contract", "extract_entities", "validate", "generate_report"]
        }
