"""Documentation Block - Auto-generates block documentation from code

Features:
- Parse docstrings and extract API signatures
- Generate markdown documentation with examples
- Interactive code playground
- Semantic search for docs
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import ast
import inspect
import re
from datetime import datetime


class DocumentationBlock(LegoBlock):
    """
    Auto-generates block documentation from code.
    API refs, usage examples, interactive playground.
    """
    name = "documentation"
    version = "1.0.0"
    requires = ["code", "vector"]
    layer = 4
    tags = ["platform", "docs", "developer", "developer_tools"]
    
    default_config = {
        "auto_generate": True,
        "include_playground": True,
        "examples_per_block": 3,
        "doc_format": "markdown",
        "theme": "github"
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.docs_cache: Dict[str, Dict] = {}  # block_id -> generated docs
        self.examples_db: Dict[str, List[Dict]] = {}  # block_id -> examples
        
    async def initialize(self) -> bool:
        """Initialize documentation generator"""
        print("📚 Documentation Block initializing...")
        print(f"   Auto-generate: {self.config['auto_generate']}")
        print(f"   Playground: {self.config['include_playground']}")
        
        # Load code block for parsing
        if hasattr(self, 'code_block') and self.code_block:
            print("   ✓ Connected to Code block")
            
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute documentation actions"""
        action = input_data.get("action")
        
        actions = {
            "generate_docs": self._generate_docs,
            "extract_signature": self._extract_signature,
            "create_playground": self._create_playground,
            "search_docs": self._search_docs,
            "add_example": self._add_example,
            "get_examples": self._get_examples,
            "render_markdown": self._render_markdown,
            "validate_docs": self._validate_docs
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _generate_docs(self, data: Dict) -> Dict:
        """Generate documentation from block source code"""
        block_id = data.get("block_id")
        block_source = data.get("source_code")
        block_instance = data.get("instance")  # Optional: live instance
        
        if not block_source and not block_id:
            return {"error": "source_code or block_id required"}
            
        # Parse the source code
        try:
            tree = ast.parse(block_source)
        except SyntaxError as e:
            return {"error": f"Invalid Python syntax: {e}"}
            
        # Extract class documentation
        docs = {
            "block_id": block_id or "unknown",
            "generated_at": datetime.utcnow().isoformat(),
            "class_docs": {},
            "methods": [],
            "config_options": [],
            "examples": []
        }
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Get class docstring
                docstring = ast.get_docstring(node)
                if docstring:
                    docs["class_docs"] = self._parse_docstring(docstring)
                    
                # Extract methods
                for item in node.body:
                    if isinstance(item, ast.AsyncFunctionDef) or isinstance(item, ast.FunctionDef):
                        method_doc = self._extract_method_docs(item)
                        if method_doc:
                            docs["methods"].append(method_doc)
                            
            elif isinstance(node, ast.Assign):
                # Look for default_config
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "default_config":
                        docs["config_options"] = self._parse_config(node.value)
                        
        # Generate markdown
        markdown = self._generate_markdown(docs)
        docs["markdown"] = markdown
        
        # Cache it
        self.docs_cache[block_id] = docs
        
        # Index in vector DB if available
        if hasattr(self, 'vector_block') and self.vector_block:
            await self.vector_block.execute({
                "action": "add",
                "documents": [markdown],
                "metadata": [{"block_id": block_id, "type": "documentation"}]
            })
            
        return {
            "generated": True,
            "block_id": block_id,
            "methods_count": len(docs["methods"]),
            "markdown_length": len(markdown),
            "preview": markdown[:500] + "..." if len(markdown) > 500 else markdown
        }
        
    async def _extract_signature(self, data: Dict) -> Dict:
        """Extract method signature from source"""
        block_source = data.get("source_code")
        method_name = data.get("method_name")
        
        if not block_source or not method_name:
            return {"error": "source_code and method_name required"}
            
        try:
            tree = ast.parse(block_source)
        except SyntaxError as e:
            return {"error": f"Invalid syntax: {e}"}
            
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if (isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)) and 
                        item.name == method_name):
                        return {
                            "method_name": method_name,
                            "signature": self._method_signature(item),
                            "docstring": ast.get_docstring(item),
                            "parameters": self._extract_params(item),
                            "returns": self._extract_returns(item)
                        }
                        
        return {"error": f"Method {method_name} not found"}
        
    async def _create_playground(self, data: Dict) -> Dict:
        """Create interactive code playground"""
        block_id = data.get("block_id")
        template = data.get("template", "basic")
        
        if not self.config["include_playground"]:
            return {"error": "Playground disabled in config"}
            
        # Generate playground templates
        templates = {
            "basic": self._generate_basic_playground(block_id),
            "advanced": self._generate_advanced_playground(block_id),
            "full_stack": self._generate_fullstack_playground(block_id)
        }
        
        playground_code = templates.get(template, templates["basic"])
        
        return {
            "playground_id": f"pg_{block_id}_{datetime.utcnow().strftime('%Y%m%d')}",
            "block_id": block_id,
            "template": template,
            "code": playground_code,
            "language": "python",
            "editable": True,
            "runnable": True
        }
        
    async def _search_docs(self, data: Dict) -> Dict:
        """Search documentation semantically"""
        query = data.get("query")
        block_filter = data.get("block_id")  # Optional
        
        if not query:
            return {"error": "Query required"}
            
        # Use vector search if available
        if hasattr(self, 'vector_block') and self.vector_block:
            results = await self.vector_block.execute({
                "action": "query",
                "query": query,
                "top_k": 5,
                "filter": {"type": "documentation"}
            })
            
            return {
                "query": query,
                "results": results.get("results", []),
                "count": len(results.get("results", []))
            }
            
        # Fallback: simple keyword search in cache
        results = []
        query_lower = query.lower()
        
        for block_id, docs in self.docs_cache.items():
            if block_filter and block_id != block_filter:
                continue
                
            score = 0
            matches = []
            
            # Search in class docs
            class_desc = docs.get("class_docs", {}).get("description", "")
            if query_lower in class_desc.lower():
                score += 0.5
                matches.append("class_description")
                
            # Search in methods
            for method in docs.get("methods", []):
                if query_lower in method.get("name", "").lower():
                    score += 0.3
                    matches.append(f"method:{method['name']}")
                    
            if score > 0:
                results.append({
                    "block_id": block_id,
                    "score": round(score, 2),
                    "matches": matches
                })
                
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "query": query,
            "results": results[:10],
            "count": len(results)
        }
        
    async def _add_example(self, data: Dict) -> Dict:
        """Add a usage example for a block"""
        block_id = data.get("block_id")
        title = data.get("title", "Untitled Example")
        code = data.get("code")
        description = data.get("description", "")
        author = data.get("author", "anonymous")
        
        if not block_id or not code:
            return {"error": "block_id and code required"}
            
        example = {
            "id": f"ex_{block_id}_{len(self.examples_db.get(block_id, []))}",
            "block_id": block_id,
            "title": title,
            "code": code,
            "description": description,
            "author": author,
            "created_at": datetime.utcnow().isoformat(),
            "votes": 0
        }
        
        if block_id not in self.examples_db:
            self.examples_db[block_id] = []
        self.examples_db[block_id].append(example)
        
        return {
            "added": True,
            "example_id": example["id"],
            "block_id": block_id
        }
        
    async def _get_examples(self, data: Dict) -> Dict:
        """Get usage examples for a block"""
        block_id = data.get("block_id")
        limit = data.get("limit", self.config["examples_per_block"])
        
        examples = self.examples_db.get(block_id, [])
        
        # Sort by votes
        examples.sort(key=lambda x: x["votes"], reverse=True)
        
        return {
            "block_id": block_id,
            "examples": examples[:limit],
            "total": len(examples)
        }
        
    async def _render_markdown(self, data: Dict) -> Dict:
        """Render markdown to HTML"""
        markdown = data.get("markdown", "")
        
        # Simple markdown to HTML conversion
        html = self._markdown_to_html(markdown)
        
        return {
            "html": html,
            "original_length": len(markdown),
            "html_length": len(html)
        }
        
    async def _validate_docs(self, data: Dict) -> Dict:
        """Validate that documentation is complete"""
        block_source = data.get("source_code")
        
        if not block_source:
            return {"error": "source_code required"}
            
        issues = []
        
        try:
            tree = ast.parse(block_source)
        except SyntaxError as e:
            return {"valid": False, "issues": [f"Syntax error: {e}"]}
            
        # Check for class docstring
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if not ast.get_docstring(node):
                    issues.append(f"Class {node.name} missing docstring")
                    
                # Check public methods
                for item in node.body:
                    if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)):
                        if not item.name.startswith("_"):
                            if not ast.get_docstring(item):
                                issues.append(f"Method {item.name} missing docstring")
                                
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "score": max(0, 1.0 - (len(issues) * 0.1))
        }
        
    # Helper methods
    def _parse_docstring(self, docstring: str) -> Dict:
        """Parse docstring into structured format"""
        lines = docstring.strip().split("\n")
        
        return {
            "description": lines[0],
            "full_text": docstring,
            "has_params": "Args:" in docstring or ":param" in docstring,
            "has_returns": "Returns:" in docstring or ":return" in docstring,
            "has_examples": "Example:" in docstring or "Examples:" in docstring
        }
        
    def _extract_method_docs(self, node) -> Optional[Dict]:
        """Extract documentation for a method"""
        docstring = ast.get_docstring(node)
        
        return {
            "name": node.name,
            "docstring": docstring,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "parameters": self._extract_params(node),
            "line_number": node.lineno
        }
        
    def _extract_params(self, node) -> List[Dict]:
        """Extract parameter info from method"""
        params = []
        
        args = node.args
        defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
        
        for arg, default in zip(args.args, defaults):
            param_info = {
                "name": arg.arg,
                "default": ast.unparse(default) if default else None,
                "annotation": ast.unparse(arg.annotation) if arg.annotation else None
            }
            params.append(param_info)
            
        return params
        
    def _extract_returns(self, node) -> Optional[str]:
        """Extract return type annotation"""
        if node.returns:
            return ast.unparse(node.returns)
        return None
        
    def _parse_config(self, node) -> List[Dict]:
        """Parse default_config dict"""
        options = []
        
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant):
                    options.append({
                        "name": key.value,
                        "default": ast.unparse(value) if hasattr(ast, 'unparse') else "..."
                    })
                    
        return options
        
    def _method_signature(self, node) -> str:
        """Generate method signature string"""
        name = node.name
        args = []
        
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)
            
        sig = f"{name}({', '.join(args)})"
        
        if node.returns:
            sig += f" -> {ast.unparse(node.returns)}"
            
        return sig
        
    def _generate_markdown(self, docs: Dict) -> str:
        """Generate markdown documentation"""
        md = f"# {docs['block_id'].title()} Block\n\n"
        
        # Class description
        class_doc = docs.get("class_docs", {})
        if class_doc.get("description"):
            md += f"{class_doc['description']}\n\n"
            
        # Configuration
        if docs.get("config_options"):
            md += "## Configuration\n\n"
            md += "| Option | Default |\n"
            md += "|--------|---------|\n"
            for opt in docs["config_options"]:
                md += f"| `{opt['name']}` | {opt['default']} |\n"
            md += "\n"
            
        # Methods
        if docs.get("methods"):
            md += "## Methods\n\n"
            for method in docs["methods"]:
                md += f"### `{method['name']}`\n\n"
                if method.get("docstring"):
                    md += f"{method['docstring']}\n\n"
                    
        return md
        
    def _generate_basic_playground(self, block_id: str) -> str:
        """Generate basic playground code"""
        class_name = block_id.title().replace('_', '') + "Block"
        return f'''# {class_name} Playground
import asyncio
from blocks.{block_id}.src.block import {class_name}

async def main():
    # Initialize the block
    block = {class_name}(config=dict())
    await block.initialize()
    
    # Try it out
    result = await block.execute({{
        "action": "test",
        "param": "value"
    }})
    
    print(result)

asyncio.run(main())
'''
        
    def _generate_advanced_playground(self, block_id: str) -> str:
        """Generate advanced playground code"""
        return self._generate_basic_playground(block_id)  # Simplified
        
    def _generate_fullstack_playground(self, block_id: str) -> str:
        """Generate full-stack playground code"""
        return self._generate_basic_playground(block_id)  # Simplified
        
    def _markdown_to_html(self, markdown: str) -> str:
        """Simple markdown to HTML conversion"""
        html = markdown
        
        # Headers
        html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        
        # Code blocks
        html = re.sub(r'```python\n(.+?)\n```', r'<pre><code class="python">\1</code></pre>', html, flags=re.DOTALL)
        html = re.sub(r'`(.+?)`', r'<code>\1</code>', html)
        
        # Paragraphs
        html = re.sub(r'\n\n', '</p><p>', html)
        
        return f"<div class=\"docs\"><p>{html}</p></div>"
        
    def health(self) -> Dict:
        h = super().health()
        h["docs_cached"] = len(self.docs_cache)
        h["examples_stored"] = sum(len(e) for e in self.examples_db.values())
        return h
