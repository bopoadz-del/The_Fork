"""Web Block - Web scraping and HTTP requests."""

from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
import aiohttp
import json


class WebBlock(BaseBlock):
    """Web scraping, HTTP requests, and HTML parsing."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="web",
            version="1.0",
            description="Web scraping and HTTP requests",
            supported_inputs=["url", "html"],
            supported_outputs=["content", "data"]
        ,
            layer=3,
            tags=["domain", "web", "scraping"]))
        self._beautifulsoup_available = self._check_bs4()
    
    def _check_bs4(self) -> bool:
        try:
            from bs4 import BeautifulSoup
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process web request."""
        params = params or {}
        operation = params.get("operation", "fetch")
        
        if operation == "fetch":
            return await self._fetch_url(input_data, params)
        elif operation == "scrape":
            return await self._scrape_content(input_data, params)
        elif operation == "api":
            return await self._api_request(input_data, params)
        elif operation == "html_parse":
            return self._parse_html(input_data, params)
        else:
            return {
                "error": f"Unknown operation: {operation}",
                "confidence": 0.0
            }
    
    async def _fetch_url(self, input_data: Any, params: Dict) -> Dict:
        """Fetch content from URL."""
        url = self._get_url(input_data)
        method = params.get("method", "GET")
        headers = params.get("headers", {})
        timeout = params.get("timeout", 30)
        follow_redirects = params.get("follow_redirects", True)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=follow_redirects
                ) as response:
                    
                    content = await response.text()
                    
                    return {
                        "url": str(response.url),
                        "status_code": response.status,
                        "content_type": response.headers.get("Content-Type", ""),
                        "content_length": len(content),
                        "content": content[:10000],  # Limit content size
                        "headers": dict(response.headers),
                        "confidence": 0.95 if response.status == 200 else 0.70
                    }
                    
        except aiohttp.ClientError as e:
            return {
                "url": url,
                "error": f"Client error: {str(e)}",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "url": url,
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _scrape_content(self, input_data: Any, params: Dict) -> Dict:
        """Scrape structured content from URL."""
        url = self._get_url(input_data)
        selectors = params.get("selectors", {})  # CSS selectors
        extract_links = params.get("extract_links", True)
        extract_text = params.get("extract_text", True)
        
        # First fetch the page
        fetch_result = await self._fetch_url(input_data, params)
        
        if "error" in fetch_result:
            return fetch_result
        
        html = fetch_result.get("content", "")
        
        if not self._beautifulsoup_available:
            return {
                "url": url,
                "error": "BeautifulSoup not available for scraping",
                "raw_content": html[:5000],
                "confidence": 0.3
            }
        
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html, "html.parser")
        
        result = {
            "url": url,
            "title": soup.title.string if soup.title else None,
        }
        
        # Extract text
        if extract_text:
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            result["text"] = "\n".join(lines)
            result["word_count"] = len(result["text"].split())
        
        # Extract links
        if extract_links:
            links = []
            for a in soup.find_all("a", href=True):
                links.append({
                    "text": a.get_text(strip=True),
                    "url": a["href"],
                    "title": a.get("title", "")
                })
            result["links"] = links
            result["link_count"] = len(links)
        
        # Custom selectors
        if selectors:
            for name, selector in selectors.items():
                elements = soup.select(selector)
                result[name] = [elem.get_text(strip=True) for elem in elements]
        
        # Extract meta tags
        meta_tags = {}
        for meta in soup.find_all("meta"):
            name = meta.get("name") or meta.get("property")
            content = meta.get("content")
            if name and content:
                meta_tags[name] = content
        result["meta_tags"] = meta_tags
        
        result["confidence"] = 0.90
        return result
    
    async def _api_request(self, input_data: Any, params: Dict) -> Dict:
        """Make API request."""
        url = self._get_url(input_data)
        method = params.get("method", "GET")
        headers = params.get("headers", {})
        data = params.get("data")
        json_data = params.get("json")
        
        # Set JSON content type if needed
        if json_data and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    json=json_data
                ) as response:
                    
                    content_type = response.headers.get("Content-Type", "")
                    
                    if "application/json" in content_type:
                        data = await response.json()
                    else:
                        data = await response.text()
                    
                    return {
                        "url": str(response.url),
                        "status_code": response.status,
                        "data": data,
                        "confidence": 0.95 if response.status < 400 else 0.5
                    }
                    
        except Exception as e:
            return {
                "url": url,
                "error": str(e),
                "confidence": 0.0
            }
    
    def _parse_html(self, input_data: Any, params: Dict) -> Dict:
        """Parse HTML content."""
        if isinstance(input_data, dict) and "content" in input_data:
            html = input_data["content"]
        elif isinstance(input_data, str):
            html = input_data
        else:
            return {"error": "Invalid HTML input", "confidence": 0.0}
        
        if not self._beautifulsoup_available:
            return {
                "raw_html": html[:5000],
                "error": "BeautifulSoup not available",
                "confidence": 0.3
            }
        
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove scripts and styles
        for script in soup(["script", "style"]):
            script.decompose()
        
        return {
            "title": soup.title.string if soup.title else None,
            "text": soup.get_text(separator="\n", strip=True),
            "headings": [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"])],
            "paragraphs": [p.get_text(strip=True) for p in soup.find_all("p")],
            "links": [{"text": a.get_text(strip=True), "url": a.get("href")} for a in soup.find_all("a", href=True)],
            "confidence": 0.90
        }
    
    def _get_url(self, input_data: Any) -> str:
        """Extract URL from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "url" in input_data:
                return input_data["url"]
            if "result" in input_data and isinstance(input_data["result"], dict):
                return input_data["result"].get("url", "")
        raise ValueError("Invalid URL input")
