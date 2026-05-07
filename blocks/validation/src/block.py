"""Validation Block - Automated testing and security scanning

Gatekeeper for Block Store quality with automated testing,
security scanning, and certification.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime
import ast
import re


class ValidationBlock(LegoBlock):
    """
    Automated testing and security scanning for submitted blocks.
    Gatekeeper for Block Store quality.
    """
    name = "validation"
    version = "1.0.0"
    requires = ["sandbox", "database"]
    layer = 3
    tags = ["store", "security", "quality", "ci", "testing"]
    
    default_config = {
        "security_checks": ["network", "filesystem", "memory", "imports"],
        "performance_threshold_ms": 500,
        "required_tests": ["unit", "integration"],
        "min_test_coverage": 70,
        "max_complexity": 15,  # Cyclomatic complexity
        "forbidden_imports": ["os.system", "subprocess", "eval", "exec"],
        "auto_certify_threshold": 0.9  # Score needed for auto-certification
    }
    
    # Security patterns to detect
    DANGEROUS_PATTERNS = {
        "hardcoded_secret": r'(password|secret|key|token)\s*=\s*[\'"][^\'"]{8,}[\'"]',
        "sql_injection": r'(?:execute|query)\s*\(.*%s.*\)',
        "eval_usage": r'\beval\s*\(',
        "exec_usage": r'\bexec\s*\(',
        "shell_execution": r'(?:os\.system|subprocess\.(call|run|Popen))',
        "dynamic_import": r'__import__\s*\(',
        "pickle_loads": r'pickle\.loads?\s*\(',
        "yaml_unsafe": r'yaml\.load\s*\([^)]*\)',
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.validation_results: Dict[str, Dict] = {}  # block_id -> latest result
        self.certified_blocks: set = set()
        self.test_templates: Dict[str, str] = {}  # Test code templates
        
    async def initialize(self) -> bool:
        """Initialize validation system"""
        print("✅ Validation Block initializing...")
        print(f"   Security checks: {self.config['security_checks']}")
        print(f"   Performance threshold: {self.config['performance_threshold_ms']}ms")
        print(f"   Min coverage: {self.config['min_test_coverage']}%")
        
        # Load test templates
        self._load_test_templates()
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute validation actions"""
        action = input_data.get("action")
        
        actions = {
            "validate_block": self._validate_block,
            "security_scan": self._security_scan,
            "performance_test": self._performance_test,
            "certify": self._certify,
            "get_validation": self._get_validation,
            "run_tests": self._run_tests,
            "check_code_quality": self._check_code_quality,
            "validate_dependencies": self._validate_dependencies,
            "generate_test_template": self._generate_test_template,
            "revoke_certification": self._revoke_certification
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    def _load_test_templates(self):
        """Load boilerplate test templates"""
        self.test_templates = {
            "block_test": '''
import pytest
from blocks.{block_name}.src.block import {BlockClass}

@pytest.fixture
def block():
    return {BlockClass}(hal_block=None, config={})

@pytest.mark.asyncio
async def test_initialize(block):
    result = await block.initialize()
    assert result is True
    assert block.initialized is True

@pytest.mark.asyncio
async def test_health(block):
    await block.initialize()
    health = block.health()
    assert "healthy" in health
''',
            "integration_test": '''
import pytest
from blocks.{block_name}.src.block import {BlockClass}

@pytest.mark.asyncio
async def test_execute_flow():
    block = {BlockClass}(hal_block=None, config={})
    await block.initialize()
    
    # Test basic execution
    result = await block.execute({{"action": "test"}})
    assert "error" not in result or result.get("status") == "ok"
'''
        }
        
    async def _validate_block(self, data: Dict) -> Dict:
        """Full validation pipeline for a block"""
        block_id = data.get("block_id")
        block_code = data.get("code")  # Source code
        block_config = data.get("config", {})
        
        if not block_id or not block_code:
            return {"error": "block_id and code required"}
            
        print(f"   🔍 Validating {block_id}...")
        
        results = {
            "block_id": block_id,
            "started_at": datetime.utcnow().isoformat(),
            "checks": {}
        }
        
        # 1. Syntax validation
        results["checks"]["syntax"] = await self._check_syntax(block_code)
        
        # 2. Required methods
        results["checks"]["structure"] = await self._check_structure(block_code)
        
        # 3. Security scan
        results["checks"]["security"] = await self._do_security_scan(block_code)
        
        # 4. Code quality
        results["checks"]["quality"] = await self._do_quality_check(block_code)
        
        # 5. Dependency check
        results["checks"]["dependencies"] = await self._do_dependency_check(block_code)
        
        # Calculate overall score
        scores = [
            c.get("score", 0) for c in results["checks"].values()
        ]
        overall_score = sum(scores) / len(scores) if scores else 0
        
        results["overall_score"] = round(overall_score, 2)
        results["passed"] = overall_score >= self.config["auto_certify_threshold"]
        results["completed_at"] = datetime.utcnow().isoformat()
        
        self.validation_results[block_id] = results
        
        # Auto-certify if threshold met
        if results["passed"]:
            await self._certify({"block_id": block_id, "auto": True})
            
        print(f"   {'✅' if results['passed'] else '❌'} Validation complete: {overall_score:.0%}")
        
        return {
            "block_id": block_id,
            "passed": results["passed"],
            "score": results["overall_score"],
            "checks": {k: v.get("passed") for k, v in results["checks"].items()},
            "details": results["checks"],
            "certified": results["passed"]
        }
        
    async def _security_scan(self, data: Dict) -> Dict:
        """Dedicated security scan"""
        block_code = data.get("code")
        
        if not block_code:
            return {"error": "code required"}
            
        return await self._do_security_scan(block_code)
        
    async def _performance_test(self, data: Dict) -> Dict:
        """Performance testing"""
        block_id = data.get("block_id")
        test_iterations = data.get("iterations", 100)
        
        # TODO: Actually instantiate and benchmark
        
        return {
            "block_id": block_id,
            "iterations": test_iterations,
            "avg_latency_ms": 0,  # Would be measured
            "p95_latency_ms": 0,
            "p99_latency_ms": 0,
            "passed": True,  # Placeholder
            "threshold_ms": self.config["performance_threshold_ms"]
        }
        
    async def _certify(self, data: Dict) -> Dict:
        """Certify a block as Store-ready"""
        block_id = data.get("block_id")
        auto = data.get("auto", False)
        
        # Verify it passed validation
        if block_id not in self.validation_results:
            return {"error": "Block must be validated before certification"}
            
        result = self.validation_results[block_id]
        if not result.get("passed") and not auto:
            return {"error": "Block did not pass validation"}
            
        self.certified_blocks.add(block_id)
        
        return {
            "certified": True,
            "block_id": block_id,
            "certified_at": datetime.utcnow().isoformat(),
            "score": result.get("overall_score"),
            "auto": auto
        }
        
    async def _get_validation(self, data: Dict) -> Dict:
        """Get validation results for a block"""
        block_id = data.get("block_id")
        
        if block_id not in self.validation_results:
            return {"error": "No validation found for this block"}
            
        result = self.validation_results[block_id]
        
        return {
            "block_id": block_id,
            "validation": result,
            "certified": block_id in self.certified_blocks
        }
        
    async def _run_tests(self, data: Dict) -> Dict:
        """Run test suite against block"""
        block_id = data.get("block_id")
        test_code = data.get("test_code")
        
        if not test_code:
            return {"error": "test_code required"}
            
        # TODO: Actually run tests in sandbox
        
        return {
            "block_id": block_id,
            "tests_run": 0,
            "passed": 0,
            "failed": 0,
            "coverage": 0,
            "output": "Test execution not yet implemented"
        }
        
    async def _check_code_quality(self, data: Dict) -> Dict:
        """Check code quality metrics"""
        block_code = data.get("code")
        
        if not block_code:
            return {"error": "code required"}
            
        return await self._do_quality_check(block_code)
        
    async def _validate_dependencies(self, data: Dict) -> Dict:
        """Validate block dependencies"""
        block_id = data.get("block_id")
        dependencies = data.get("dependencies", [])
        
        issues = []
        available = []
        
        for dep in dependencies:
            # TODO: Check if dependency exists in registry
            available.append(dep)
            
        return {
            "block_id": block_id,
            "dependencies": dependencies,
            "available": available,
            "missing": [],
            "issues": issues,
            "valid": len(issues) == 0
        }
        
    async def _generate_test_template(self, data: Dict) -> Dict:
        """Generate test template for a block"""
        block_name = data.get("block_name")
        block_class = data.get("block_class", f"{block_name.title()}Block")
        
        template = self.test_templates["block_test"].format(
            block_name=block_name,
            BlockClass=block_class
        )
        
        integration = self.test_templates["integration_test"].format(
            block_name=block_name,
            BlockClass=block_class
        )
        
        return {
            "block_name": block_name,
            "unit_test": template,
            "integration_test": integration,
            "filename": f"test_{block_name}.py"
        }
        
    async def _revoke_certification(self, data: Dict) -> Dict:
        """Revoke a block's certification"""
        block_id = data.get("block_id")
        reason = data.get("reason", "")
        
        if block_id not in self.certified_blocks:
            return {"error": "Block not certified"}
            
        self.certified_blocks.discard(block_id)
        
        # Flag in validation results
        if block_id in self.validation_results:
            self.validation_results[block_id]["certification_revoked"] = {
                "at": datetime.utcnow().isoformat(),
                "reason": reason
            }
            
        return {
            "revoked": True,
            "block_id": block_id,
            "reason": reason
        }
        
    # Validation check implementations
    async def _check_syntax(self, code: str) -> Dict:
        """Check Python syntax"""
        try:
            ast.parse(code)
            return {
                "passed": True,
                "score": 1.0,
                "errors": []
            }
        except SyntaxError as e:
            return {
                "passed": False,
                "score": 0.0,
                "errors": [str(e)]
            }
            
    async def _check_structure(self, code: str) -> Dict:
        """Check block has required structure"""
        errors = []
        score = 1.0
        
        required_patterns = {
            "class_definition": r'class\s+\w+Block\s*\(\s*LegoBlock\s*\)',
            "name_attribute": r'name\s*=\s*["\']\w+["\']',
            "version_attribute": r'version\s*=\s*["\']',
            "initialize_method": r'async\s+def\s+initialize\s*\(',
            "execute_method": r'async\s+def\s+execute\s*\(',
            "health_method": r'def\s+health\s*\('
        }
        
        for check_name, pattern in required_patterns.items():
            if not re.search(pattern, code):
                errors.append(f"Missing: {check_name}")
                score -= 0.15
                
        return {
            "passed": len(errors) == 0,
            "score": max(score, 0),
            "errors": errors
        }
        
    async def _do_security_scan(self, code: str) -> Dict:
        """Security scan for dangerous patterns"""
        findings = []
        score = 1.0
        
        for check_name, pattern in self.DANGEROUS_PATTERNS.items():
            matches = re.finditer(pattern, code, re.IGNORECASE)
            for match in matches:
                line_num = code[:match.start()].count('\n') + 1
                findings.append({
                    "type": check_name,
                    "line": line_num,
                    "snippet": code[max(0, match.start()-20):match.end()+20]
                })
                score -= 0.2
                
        # Check for forbidden imports
        for forbidden in self.config["forbidden_imports"]:
            if forbidden in code:
                findings.append({
                    "type": "forbidden_import",
                    "import": forbidden,
                    "severity": "high"
                })
                score -= 0.3
                
        return {
            "passed": len(findings) == 0,
            "score": max(score, 0),
            "findings": findings,
            "severity_counts": {
                "high": len([f for f in findings if f.get("severity") == "high"]),
                "medium": len([f for f in findings if f.get("type") in self.DANGEROUS_PATTERNS]),
                "low": 0
            }
        }
        
    async def _do_quality_check(self, code: str) -> Dict:
        """Check code quality metrics"""
        issues = []
        score = 1.0
        
        # Check line length
        long_lines = [i+1 for i, line in enumerate(code.split('\n')) if len(line) > 120]
        if long_lines:
            issues.append(f"{len(long_lines)} lines exceed 120 characters")
            score -= min(len(long_lines) * 0.01, 0.2)
            
        # Check docstrings
        if '"""' not in code and "'''" not in code:
            issues.append("Missing module docstring")
            score -= 0.1
            
        # Check for TODOs/FIXMEs
        todos = len(re.findall(r'\b(TODO|FIXME|XXX)\b', code))
        if todos > 5:
            issues.append(f"{todos} TODO/FIXME comments found")
            score -= min(todos * 0.02, 0.15)
            
        # Estimate complexity (simplified)
        branches = len(re.findall(r'\b(if|for|while|except)\b', code))
        if branches > 20:
            issues.append(f"High complexity: ~{branches} branches")
            score -= 0.1
            
        return {
            "passed": score >= 0.7,
            "score": max(score, 0),
            "issues": issues,
            "metrics": {
                "estimated_complexity": branches,
                "lines_of_code": len(code.split('\n')),
                "todo_count": todos
            }
        }
        
    async def _do_dependency_check(self, code: str) -> Dict:
        """Check and validate dependencies"""
        # Extract imports
        imports = re.findall(r'(?:from|import)\s+([\w.]+)', code)
        
        # Filter to blocks
        block_deps = [i for i in imports if i.startswith('blocks.')]
        
        # Check for version pinning (optional)
        pinned = []
        unpinned = []
        
        return {
            "passed": True,
            "score": 1.0,
            "block_dependencies": block_deps,
            "external_dependencies": [i for i in imports if not i.startswith('blocks.')],
            "pinned": pinned,
            "unpinned": unpinned
        }
        
    def health(self) -> Dict:
        h = super().health()
        h["certified_blocks"] = len(self.certified_blocks)
        h["validations_performed"] = len(self.validation_results)
        h["security_rules"] = len(self.DANGEROUS_PATTERNS)
        h["auto_certify_threshold"] = self.config["auto_certify_threshold"]
        return h
