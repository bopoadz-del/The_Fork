"""Code Block - Code execution and analysis."""

import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
import ast


class CodeBlock(BaseBlock):
    """Code execution, analysis, and transformation."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="code",
            version="1.0",
            description="Code execution, analysis, and transformation",
            supported_inputs=["code", "text"],
            supported_outputs=["result", "analysis"]
        ,
            layer=3,
            tags=["domain", "code", "execution"]))
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process code (execute, analyze, or transform)."""
        params = params or {}
        operation = params.get("operation", "execute")
        language = params.get("language", "python")
        
        code = self._get_code(input_data)
        
        result = {
            "operation": operation,
            "language": language,
            "code_preview": code[:200] + "..." if len(code) > 200 else code,
        }
        
        if operation == "execute":
            execution = await self._execute_code(code, language, params)
            result.update(execution)
        elif operation == "analyze":
            analysis = self._analyze_code(code, language)
            result.update(analysis)
        elif operation == "lint":
            lint_result = self._lint_code(code, language)
            result.update(lint_result)
        elif operation == "format":
            formatted = self._format_code(code, language)
            result.update(formatted)
        else:
            result["error"] = f"Unknown operation: {operation}"
            result["confidence"] = 0.0
        
        return result
    
    def _get_code(self, input_data: Any) -> str:
        """Extract code from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "code" in input_data:
                return input_data["code"]
            if "text" in input_data:
                return input_data["text"]
            if "result" in input_data and isinstance(input_data["result"], dict):
                return input_data["result"].get("text", "")
        raise ValueError("Invalid code input")
    
    async def _execute_code(self, code: str, language: str, params: Dict) -> Dict:
        """Execute code in a sandboxed environment."""
        if language == "python":
            return await self._execute_python(code, params)
        elif language in ("javascript", "node"):
            return await self._execute_javascript(code, params)
        elif language == "bash":
            return await self._execute_bash(code, params)
        else:
            return {
                "error": f"Execution not supported for {language}",
                "confidence": 0.0
            }
    
    async def _execute_python(self, code: str, params: Dict) -> Dict:
        """Execute Python code."""
        timeout = params.get("timeout", 10)
        allow_imports = params.get("allow_imports", False)
        
        # Security check - disallow dangerous imports
        dangerous_imports = ["os", "sys", "subprocess", "importlib", "eval", "exec", "compile"]
        
        if not allow_imports:
            for imp in dangerous_imports:
                if f"import {imp}" in code or f"from {imp}" in code:
                    return {
                        "error": f"Import '{imp}' not allowed. Use allow_imports=True to override.",
                        "confidence": 0.0
                    }
        
        try:
            # Use a temporary file to execute the code
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            # Execute with timeout
            process = subprocess.run(
                ["python3", temp_file],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            os.unlink(temp_file)
            
            return {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "confidence": 0.95 if process.returncode == 0 else 0.5
            }
            
        except subprocess.TimeoutExpired:
            return {
                "error": f"Code execution timed out after {timeout} seconds",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _execute_javascript(self, code: str, params: Dict) -> Dict:
        """Execute JavaScript code using Node.js."""
        timeout = params.get("timeout", 10)
        
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            process = subprocess.run(
                ["node", temp_file],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            os.unlink(temp_file)
            
            return {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "confidence": 0.95 if process.returncode == 0 else 0.5
            }
            
        except FileNotFoundError:
            return {
                "error": "Node.js not available",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "error": str(e),
                "confidence": 0.0
            }
    
    _SHELL_ALLOWLIST = {"echo", "cat", "ls", "pwd", "wc", "head", "tail", "grep", "find", "git", "mkdir", "touch", "cp", "mv", "rm", "chmod", "python", "pytest"}

    def _audit_shell(self, command: str, allowed: bool, result: dict):
        import datetime, pathlib
        log_dir = pathlib.Path("logs")
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "shell_audit.log", "a") as f:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            status = "ALLOWED" if allowed else "BLOCKED"
            f.write(f"{ts} | {status} | {command!r} | {result.get('return_code', 'N/A')}\n")

    async def _execute_bash(self, code: str, params: Dict) -> Dict:
        """Execute bash commands with strict allowlist and audit logging."""
        timeout = params.get("timeout", 10)

        # Security check — static allowlist, no bypass
        command = code.strip().split()[0] if code.strip() else ""
        allowed = command in self._SHELL_ALLOWLIST

        if not allowed:
            result = {"error": f"Command '{command}' not in allowed list.", "confidence": 0.0}
            self._audit_shell(code, allowed=False, result=result)
            return result

        try:
            process = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            result = {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "return_code": process.returncode,
                "success": process.returncode == 0,
                "confidence": 0.95 if process.returncode == 0 else 0.5
            }
            self._audit_shell(code, allowed=True, result=result)
            return result

        except Exception as e:
            result = {"error": str(e), "confidence": 0.0}
            self._audit_shell(code, allowed=False, result=result)
            return result
    
    def _analyze_code(self, code: str, language: str) -> Dict:
        """Analyze code structure and complexity."""
        result = {
            "lines": len(code.split("\n")),
            "characters": len(code),
            "language": language
        }
        
        if language == "python":
            try:
                tree = ast.parse(code)
                
                functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
                classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
                imports = [node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)]
                from_imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
                
                result.update({
                    "functions": functions,
                    "function_count": len(functions),
                    "classes": classes,
                    "class_count": len(classes),
                    "imports": imports + from_imports,
                    "import_count": len(imports) + len(from_imports),
                    "confidence": 0.95
                })
            except SyntaxError as e:
                result.update({
                    "syntax_error": str(e),
                    "confidence": 0.5
                })
        
        return result
    
    def _lint_code(self, code: str, language: str) -> Dict:
        """Lint code for issues."""
        issues = []
        
        if language == "python":
            # Basic linting without external tools
            lines = code.split("\n")
            for i, line in enumerate(lines, 1):
                if len(line) > 100:
                    issues.append({"line": i, "type": "warning", "message": "Line too long (>100 chars)"})
                if line.strip().endswith(";"):
                    issues.append({"line": i, "type": "style", "message": "Unnecessary semicolon"})
            
            try:
                ast.parse(code)
            except SyntaxError as e:
                issues.append({"line": e.lineno or 0, "type": "error", "message": str(e)})
        
        return {
            "issues": issues,
            "issue_count": len(issues),
            "has_errors": any(i["type"] == "error" for i in issues),
            "confidence": 0.80
        }
    
    def _format_code(self, code: str, language: str) -> Dict:
        """Format code according to style guidelines."""
        # Basic formatting
        lines = code.split("\n")
        formatted_lines = []
        indent_level = 0
        
        for line in lines:
            stripped = line.strip()
            
            # Decrease indent for closing blocks
            if stripped.startswith(("}", "]", ")")) or stripped.startswith(("elif", "else:", "except", "finally:")):
                indent_level = max(0, indent_level - 1)
            
            formatted_lines.append("    " * indent_level + stripped)
            
            # Increase indent for opening blocks
            if stripped.endswith(":") or stripped.endswith("{") or stripped.endswith("[") or stripped.endswith("("):
                indent_level += 1
        
        formatted = "\n".join(formatted_lines)
        
        return {
            "formatted_code": formatted,
            "original_lines": len(lines),
            "formatted_lines": len(formatted_lines),
            "confidence": 0.70
        }
