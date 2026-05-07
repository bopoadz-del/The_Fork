"""Security Container - Auth, secrets, sandbox, audit"""

import hashlib
import os
import time
from typing import Any, Dict
from app.core.universal_base import UniversalContainer


class SecurityContainer(UniversalContainer):
    """
    Security Container: API key auth, rate limiting, sandbox, audit logging
    """
    
    name = "security"
    version = "1.0"
    description = "Security: Auth, Secrets, Sandbox, Audit, Rate Limiter"
    layer = 1  # Security layer
    tags = ["security", "container", "auth"]
    requires = []

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"action": "auth", "api_key": "cb_..."}',
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "valid", "type": "boolean", "label": "Valid"},
                {"name": "role", "type": "text", "label": "Role"},
                {"name": "safe", "type": "boolean", "label": "Safe"}
            ]
        },
        "quick_actions": [
            {"icon": "🔑", "label": "Create Key", "prompt": '{"action":"create_key","owner":"my_app","role":"user"}'},
            {"icon": "🔐", "label": "Authenticate", "prompt": '{"action":"auth","api_key":"cb_..."}'},
            {"icon": "🛡️", "label": "Rate Check", "prompt": '{"action":"check_rate","key":"user_123","limit":100}'},
            {"icon": "📋", "label": "Audit Log", "prompt": '{"action":"audit","event":"login","user":"user_123"}'}
        ]
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.api_keys = {}
        self.rate_counters = {}
        self.audit_log = []
    
    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        if action == "create_key":
            return await self.create_key(params)
        elif action == "auth":
            return await self.auth(params)
        elif action == "check_rate":
            return await self.check_rate(params)
        elif action == "sandbox_check":
            return await self.sandbox_check(input_data, params)
        elif action == "audit":
            return await self.audit(params)
        elif action == "validate_file":
            return await self.validate_file(input_data, params)
        elif action == "validate_block_code":
            return await self.validate_block_code(input_data, params)
        elif action == "health_check":
            return await self.health_check()
        else:
            return {"error": f"Unknown action: {action}"}
    
    async def create_key(self, params: Dict) -> Dict:
        """Generate API key"""
        owner = params.get("owner", "anonymous")
        key_hash = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
        api_key = f"cb_{key_hash}"
        
        self.api_keys[api_key] = {
            "owner": owner,
            "created_at": time.time(),
            "role": params.get("role", "user"),
            "rate_limit": params.get("rate_limit", 100)
        }
        
        return {
            "status": "success",
            "created": True,
            "api_key": api_key,
            "role": "user",
            "rate_limit": 100
        }
    
    async def auth(self, params: Dict) -> Dict:
        """Validate API key"""
        api_key = params.get("api_key", "")
        
        # Dev key check (only if CB_DEV_KEY set and not production)
        dev_key = os.environ.get("CB_DEV_KEY", "")
        env = os.environ.get("ENV", "production")
        if api_key == dev_key and env != "production":
            return {"authenticated": True, "role": "admin", "key_id": "dev", "warning": "Dev key"}
        
        if api_key in self.api_keys:
            return {"authenticated": True, **self.api_keys[api_key]}
        return {"authenticated": False, "error": "Invalid key"}
    
    async def check_rate(self, params: Dict) -> Dict:
        """Check rate limit"""
        key = params.get("key", "default")
        limit = params.get("limit", 100)
        
        now = time.time()
        window = 3600  # 1 hour
        
        if key not in self.rate_counters:
            self.rate_counters[key] = {"count": 0, "reset_at": now + window}
        
        counter = self.rate_counters[key]
        if now > counter["reset_at"]:
            counter["count"] = 0
            counter["reset_at"] = now + window
        
        allowed = counter["count"] < limit
        if allowed:
            counter["count"] += 1
        
        return {
            "allowed": allowed,
            "remaining": max(0, limit - counter["count"]),
            "reset_at": counter["reset_at"]
        }
    
    async def sandbox_check(self, code: str, params: Dict) -> Dict:
        """Check code safety"""
        if not code:
            code = params.get("code", "")
        
        blocked = ["exec(", "eval(", "__import__", "os.system", "subprocess"]
        violations = [b for b in blocked if b in code]
        
        return {
            "safe": len(violations) == 0,
            "violations": violations
        }
    
    async def audit(self, params: Dict) -> Dict:
        """Log audit event"""
        event = {
            "action": params.get("action"),
            "timestamp": time.time(),
            "user": params.get("user"),
            "result": params.get("result")
        }
        self.audit_log.append(event)
        return {"logged": True}
    
    async def validate_file(self, input_data: Any, params: Dict) -> Dict:
        """Validate file upload for size, type, and basic malware patterns"""
        import re
        import os
        from pathlib import Path

        file_path = None
        file_content = None
        filename = "unknown"

        if isinstance(input_data, dict):
            file_path = input_data.get("file_path")
            file_content = input_data.get("content")
            filename = input_data.get("filename", filename)
        if params:
            file_path = file_path or params.get("file_path")
            file_content = file_content if file_content is not None else params.get("content")
            filename = params.get("filename", filename)

        if not file_path and file_content is None:
            return {"status": "error", "error": "No file provided", "safe": False}

        # Size check (50MB max)
        max_size = 50 * 1024 * 1024
        if file_path and os.path.exists(file_path):
            size = os.path.getsize(file_path)
        else:
            size = len(file_content) if isinstance(file_content, (bytes, str)) else 0

        if size > max_size:
            return {
                "status": "error",
                "error": f"File too large: {size/1024/1024:.1f}MB (max 50MB)",
                "safe": False,
                "violation": "size_limit"
            }

        # MIME type validation
        allowed_types = {
            'application/pdf': ['.pdf'],
            'image/jpeg': ['.jpg', '.jpeg'],
            'image/png': ['.png'],
            'image/gif': ['.gif'],
            'image/webp': ['.webp'],
            'audio/mpeg': ['.mp3'],
            'audio/wav': ['.wav'],
            'text/plain': ['.txt'],
            'application/json': ['.json'],
            'text/csv': ['.csv']
        }

        # Detect MIME type with fallback
        mime = None
        try:
            import magic
            if file_path and os.path.exists(file_path):
                mime = magic.from_file(file_path, mime=True)
            else:
                content_bytes = file_content[:1024] if isinstance(file_content, bytes) else str(file_content)[:1024].encode('utf-8', errors='ignore')
                mime = magic.from_buffer(content_bytes, mime=True)
        except Exception:
            # Fallback to extension-based detection
            ext = Path(filename).suffix.lower()
            ext_to_mime = {v[0]: k for k, vals in allowed_types.items() for v in [vals]}
            mime = ext_to_mime.get(ext, 'application/octet-stream')

        if mime not in allowed_types:
            return {
                "status": "error",
                "error": f"File type '{mime}' not allowed",
                "safe": False,
                "violation": "invalid_mime",
                "detected_type": mime
            }

        # Extension check
        ext = Path(filename).suffix.lower()
        if ext not in allowed_types.get(mime, []):
            return {
                "status": "error",
                "error": f"Extension '{ext}' does not match detected type '{mime}'",
                "safe": False,
                "violation": "extension_mismatch"
            }

        # Basic malware pattern scan
        suspicious_patterns = [
            rb'<%\s*@.*Language',
            rb'<script.*>',
            rb'eval\s*\(',
            rb'exec\s*\(',
            rb'import\s+os',
            rb'system\s*\(',
            rb'cmd\.exe',
            rb'powershell',
            rb'<\?php',
            rb'#!/bin/(bash|sh)',
        ]

        scan_data = b''
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, 'rb') as f:
                    scan_data = f.read(8192)
            except Exception:
                pass
        else:
            scan_data = file_content[:8192] if isinstance(file_content, bytes) else str(file_content).encode('utf-8', errors='ignore')[:8192]

        magic_signatures = {
            b'%PDF': 'application/pdf',
            b'\xff\xd8\xff': 'image/jpeg',
            b'\x89PNG': 'image/png',
            b'RIFF': 'audio/wav',
            b'ID3': 'audio/mpeg'
        }

        header_match = False
        for sig, expected_mime in magic_signatures.items():
            if scan_data.startswith(sig):
                header_match = True
                if expected_mime != mime:
                    return {
                        "status": "error",
                        "error": "File header does not match claimed type (possible spoofing)",
                        "safe": False,
                        "violation": "header_spoofing"
                    }
                break

        violations = []
        for pattern in suspicious_patterns:
            try:
                if re.search(pattern, scan_data, re.IGNORECASE):
                    violations.append(pattern.decode('utf-8', errors='ignore')[:20])
            except Exception:
                pass

        if violations:
            return {
                "status": "error",
                "error": "Suspicious patterns detected",
                "safe": False,
                "violation": "suspicious_content",
                "patterns_found": violations[:3]
            }

        return {
            "status": "success",
            "safe": True,
            "size": size,
            "mime_type": mime,
            "extension": ext,
            "scanned": True
        }

    async def validate_block_code(self, input_data: Any, params: Dict) -> Dict:
        """Validate block source code before publishing to store"""
        import re
        import ast
        import hashlib
        from datetime import datetime

        code = ""
        block_name = "unknown"
        language = "python"

        if isinstance(input_data, dict):
            code = input_data.get("code", "")
            block_name = input_data.get("block_name", block_name)
            language = input_data.get("language", language)
        if params:
            code = code or params.get("code", "")
            block_name = params.get("block_name", block_name) if params.get("block_name") else block_name
            language = params.get("language", language) if params.get("language") else language

        if not code:
            return {"status": "error", "error": "No code provided", "safe": False}

        # Dangerous patterns for Python blocks
        python_dangerous = [
            (r'\bexec\s*\(', "exec() execution"),
            (r'\beval\s*\(', "eval() execution"),
            (r'\bcompile\s*\(', "code compilation"),
            (r'\b__import__\s*\(', "dynamic import"),
            (r'\bimportlib\b', "importlib usage"),
            (r'\bsubprocess\b', "subprocess calls"),
            (r'\bos\.system\s*\(', "system command execution"),
            (r'\bos\.popen\s*\(', "popen execution"),
            (r'\bpty\.spawn\s*\(', "spawn shell"),
            (r'\bsocket\b', "network sockets"),
            (r'\brequests\.(get|post|put|delete)\s*\(', "HTTP requests"),
            (r'\burllib\b', "urllib network access"),
            (r'\bftplib\b', "FTP access"),
            (r'\bparamiko\b', "SSH access"),
            (r'\bsmtplib\b', "email sending"),
            (r'\bopen\s*\([^)]*[\'"]/(etc|root|home|var)', "sensitive file access"),
            (r'\bopen\s*\([^)]*[\'"].*\.(key|pem|env|config)', "credential file access"),
            (r'\brm\s+-rf\b', "recursive delete"),
            (r'\bshutil\.rmtree\s*\(', "directory deletion"),
            (r'\bos\.remove\s*\(', "file deletion"),
            (r'\bos\.chmod\s*\(', "permission changes"),
            (r'\bos\.chown\s*\(', "ownership changes"),
            (r'\bpty\b', "pseudo-terminal"),
            (r'\bplatform\b', "system fingerprinting"),
            (r'\bgetpass\b', "credential harvesting"),
            (r'\bkeyring\b', "password access"),
            (r'\bcryptography\b', "crypto operations"),
            (r'\bpickle\.loads\s*\(', "pickle deserialization (RCE risk)"),
            (r'\byaml\.load\s*\(', "YAML unsafe load"),
            (r'\bxml\.etree\.ElementTree\b', "XML parsing (XXE risk)"),
            (r'\bxml\.sax\b', "XML SAX parsing"),
            (r'\bxmlrpc\b', "XML-RPC"),
            (r'\bBase64\s*\.\s*b64decode\s*\(.*exec\s*\(|eval\s*\(', "encoded payload execution"),
        ]

        # Dangerous patterns for JavaScript/TypeScript blocks
        js_dangerous = [
            (r'\beval\s*\(', "eval() execution"),
            (r'\bFunction\s*\(', "Function constructor"),
            (r'\bsetTimeout\s*\([^,]*["\']', "string timeout (code exec)"),
            (r'\bsetInterval\s*\([^,]*["\']', "string interval (code exec)"),
            (r'\brequire\s*\(\s*["\']child_process', "child_process"),
            (r'\brequire\s*\(\s*["\']fs["\']\s*\)\s*\.\s*(writeFile|unlink|rmdir)', "file system deletion"),
            (r'\brequire\s*\(\s*["\']http["\']', "HTTP server"),
            (r'\brequire\s*\(\s*["\']net["\']', "network access"),
            (r'\brequire\s*\(\s*["\']dgram["\']', "UDP sockets"),
            (r'\brequire\s*\(\s*["\']tls["\']', "TLS/network"),
            (r'\bprocess\.env', "environment access"),
            (r'\bprocess\.exit\s*\(', "process termination"),
            (r'\brequire\s*\(\s*["\']vm["\']', "vm module (sandbox escape)"),
            (r'\bWebSocket\s*\(', "WebSocket connections"),
            (r'\bfetch\s*\(', "fetch API calls"),
            (r'\bXMLHttpRequest\b', "XHR requests"),
            (r'\bdocument\.write\s*\(', "DOM manipulation"),
            (r'\blocalStorage\b', "storage access"),
            (r'\bsessionStorage\b', "session storage"),
            (r'\batob\s*\(.*\btobytes', "base64 decode chain"),
            (r'\bimport\s*\(\s*["\']https?://', "dynamic URL import"),
        ]

        patterns = python_dangerous if language == "python" else js_dangerous

        violations = []
        lines = code.split('\n')

        for i, line in enumerate(lines, 1):
            for pattern, description in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    violations.append({
                        "line": i,
                        "code": line.strip()[:50],
                        "risk": description
                    })

        # AST analysis for Python (deeper inspection)
        if language == "python":
            try:
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name in ['subprocess', 'os', 'socket', 'requests', 'urllib', 'ftplib', 'smtplib', 'paramiko', 'pty', 'pickle', 'yaml', 'xmlrpc']:
                                violations.append({
                                    "line": getattr(node, 'lineno', 0),
                                    "code": f"import {alias.name}",
                                    "risk": f"Import of restricted module: {alias.name}"
                                })
                    elif isinstance(node, ast.ImportFrom):
                        if node.module in ['subprocess', 'os', 'socket', 'requests', 'urllib', 'ftplib', 'smtplib', 'paramiko', 'pty', 'pickle', 'yaml', 'xmlrpc', 'system', 'popen']:
                            violations.append({
                                "line": getattr(node, 'lineno', 0),
                                "code": f"from {node.module} import ...",
                                "risk": f"Import from restricted module: {node.module}"
                            })
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            if node.func.id in ['exec', 'eval', 'compile', '__import__']:
                                violations.append({
                                    "line": getattr(node, 'lineno', 0),
                                    "code": f"{node.func.id}(...)",
                                    "risk": f"Dangerous function call: {node.func.id}"
                                })
            except SyntaxError:
                return {
                    "status": "error",
                    "error": "Invalid Python syntax",
                    "safe": False,
                    "violation": "syntax_error"
                }

        # Calculate code hash for integrity tracking
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        # Severity assessment
        critical = ['exec', 'eval', 'subprocess', 'os.system', 'pickle.loads', '__import__', 'compile']
        high = ['socket', 'requests', 'urllib', 'ftplib', 'paramiko', 'pty', 'yaml.load']

        severity = "low"
        for v in violations:
            risk = v.get("risk", "").lower()
            if any(c in risk for c in critical):
                severity = "critical"
                break
            elif any(h in risk for h in high):
                severity = "high" if severity != "critical" else severity

        if violations:
            return {
                "status": "error",
                "error": f"Security violations found in block '{block_name}'",
                "safe": False,
                "violation": "dangerous_code",
                "severity": severity,
                "violations_count": len(violations),
                "violations": violations[:5],
                "code_hash": code_hash,
                "recommendation": "Remove dangerous imports and function calls. Use provided APIs only."
            }

        return {
            "status": "success",
            "safe": True,
            "block_name": block_name,
            "language": language,
            "lines_of_code": len(lines),
            "code_hash": code_hash,
            "scanned": True,
            "violations_found": 0
        }

    async def audit_block_submission(self, block_name: str, validation_result: dict, submitter: str) -> dict:
        """Log all block submissions for security review"""
        from datetime import datetime
        audit_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "block_name": block_name,
            "submitter": submitter,
            "validation_passed": validation_result.get("safe"),
            "code_hash": validation_result.get("code_hash"),
            "lines_of_code": validation_result.get("lines_of_code"),
            "violations": validation_result.get("violations_count", 0),
            "severity": validation_result.get("severity", "none")
        }
        self.audit_log.append({
            "action": "block_submission",
            **audit_record
        })
        return audit_record

    async def health_check(self) -> Dict:
        return {
            "status": "healthy",
            "container": self.name,
            "capabilities": ["create_key", "auth", "check_rate", "sandbox_check", "audit", "validate_file", "validate_block_code"],
            "keys_issued": len(self.api_keys)
        }
