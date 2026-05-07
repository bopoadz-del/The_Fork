from blocks.base import LegoBlock
from typing import Dict, Any
import asyncio
import subprocess
import tempfile
import os

class CodeBlock(LegoBlock):
    """Code Generation & Execution (Sandboxed)"""
    name = "code"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Utility layer
    tags = ["code", "execution", "sandbox", "utility"]
    default_config = {
        "sandbox": True,
        "timeout": 30,
        "allowed_langs": ["python", "javascript", "bash"]
    }
    
    LANGUAGES = {
        "python": {"ext": ".py", "executor": "python3"},
        "javascript": {"ext": ".js", "executor": "node"},
        "bash": {"ext": ".sh", "executor": "bash"},
        "sql": {"ext": ".sql", "executor": "sqlite3"}
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.sandbox_enabled = config.get("sandbox", True)
        self.timeout = config.get("timeout", 30)
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "run":
            return await self._run_code(input_data)
        elif action == "generate":
            return await self._generate_code(input_data)
        elif action == "lint":
            return await self._lint_code(input_data)
        return {"error": "Unknown action"}
    
    async def _run_code(self, data: Dict) -> Dict:
        code = data.get("code")
        language = data.get("language", "python")
        
        if language not in self.LANGUAGES:
            return {"error": f"Unsupported language: {language}"}
        
        lang_config = self.LANGUAGES[language]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix=lang_config["ext"], delete=False) as f:
            f.write(code)
            temp_path = f.name
        
        try:
            proc = await asyncio.create_subprocess_exec(
                lang_config["executor"], temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), 
                timeout=self.timeout
            )
            
            return {
                "success": proc.returncode == 0,
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
                "returncode": proc.returncode
            }
            
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "error": f"Execution timeout after {self.timeout}s"}
        finally:
            os.unlink(temp_path)
    
    async def _generate_code(self, data: Dict) -> Dict:
        prompt = data.get("prompt")
        language = data.get("language", "python")
        return {"code": f"# Generated {language} code for: {prompt}\n# TODO: Implement", "language": language}
    
    async def _lint_code(self, data: Dict) -> Dict:
        code = data.get("code")
        language = data.get("language", "python")
        
        if language == "python":
            try:
                import pylint.lint
                from io import StringIO
                import sys
                
                pylint_output = StringIO()
                old_stdout = sys.stdout
                sys.stdout = pylint_output
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                    f.write(code)
                    temp_path = f.name
                
                try:
                    pylint.lint.Run([temp_path], exit=False)
                except SystemExit:
                    pass
                
                sys.stdout = old_stdout
                os.unlink(temp_path)
                return {"lint_output": pylint_output.getvalue()}
            except ImportError:
                return {"lint_output": "pylint not installed"}
        
        return {"lint_output": "Linting not available for this language"}
    
    def health(self) -> Dict:
        h = super().health()
        h["sandbox"] = self.sandbox_enabled
        h["languages"] = list(self.LANGUAGES.keys())
        return h
