"""Search Block - Web search capabilities."""

import os
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
import aiohttp


class SearchBlock(BaseBlock):
    """Web search using various providers (Serper, Bing, DuckDuckGo)."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="search",
            version="1.0",
            description="Web search using Serper, Bing, or DuckDuckGo",
            requires_api_key=True,
            supported_inputs=["query"],
            supported_outputs=["results"]
        ,
            layer=3,
            tags=["domain", "search", "web"]))
        self._duckduckgo_available = self._check_duckduckgo()
    
    def _check_duckduckgo(self) -> bool:
        try:
            from duckduckgo_search import DDGS
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Perform web search."""
        params = params or {}
        provider = params.get("provider", "duckduckgo")
        num_results = params.get("num_results", 10)
        
        query = self._get_query(input_data)
        
        result = {
            "query": query,
            "provider": provider,
        }
        
        if provider == "serper":
            search_results = await self._search_serper(query, num_results)
            result.update(search_results)
        elif provider == "bing":
            search_results = await self._search_bing(query, num_results)
            result.update(search_results)
        elif provider == "duckduckgo" and self._duckduckgo_available:
            search_results = await self._search_duckduckgo(query, num_results)
            result.update(search_results)
        elif provider == "mock":
            result["results"] = [
                {"title": f"Mock result {i}", "url": f"https://example.com/{i}", "snippet": f"This is mock result {i} for query: {query}"}
                for i in range(num_results)
            ]
            result["total_results"] = num_results
            result["confidence"] = 1.0
        else:
            result["results"] = []
            result["error"] = f"Provider {provider} not available"
            result["confidence"] = 0.0
        
        return result
    
    def _get_query(self, input_data: Any) -> str:
        """Extract query from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "query" in input_data:
                return input_data["query"]
            if "text" in input_data:
                return input_data["text"]
            if "result" in input_data and isinstance(input_data["result"], dict):
                return input_data["result"].get("text", "")
        raise ValueError("Invalid query input")
    
    async def _search_serper(self, query: str, num_results: int) -> Dict:
        """Search using Serper.dev (Google Search API)."""
        api_key = os.getenv("SERPER_API_KEY")
        if not api_key:
            return {
                "results": [],
                "error": "SERPER_API_KEY not set",
                "confidence": 0.0
            }
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
                payload = {"q": query, "num": num_results}
                
                async with session.post(
                    "https://google.serper.dev/search",
                    headers=headers,
                    json=payload
                ) as response:
                    data = await response.json()
                    
                    results = []
                    for item in data.get("organic", []):
                        results.append({
                            "title": item.get("title"),
                            "url": item.get("link"),
                            "snippet": item.get("snippet"),
                            "position": item.get("position")
                        })
                    
                    return {
                        "results": results,
                        "total_results": len(results),
                        "search_metadata": data.get("searchParameters", {}),
                        "confidence": 0.92
                    }
        except Exception as e:
            return {
                "results": [],
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _search_bing(self, query: str, num_results: int) -> Dict:
        """Search using Bing Search API."""
        api_key = os.getenv("BING_SEARCH_API_KEY")
        if not api_key:
            return {
                "results": [],
                "error": "BING_SEARCH_API_KEY not set",
                "confidence": 0.0
            }
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Ocp-Apim-Subscription-Key": api_key}
                params = {"q": query, "count": num_results}
                
                async with session.get(
                    "https://api.bing.microsoft.com/v7.0/search",
                    headers=headers,
                    params=params
                ) as response:
                    data = await response.json()
                    
                    results = []
                    for item in data.get("webPages", {}).get("value", []):
                        results.append({
                            "title": item.get("name"),
                            "url": item.get("url"),
                            "snippet": item.get("snippet")
                        })
                    
                    return {
                        "results": results,
                        "total_results": len(results),
                        "confidence": 0.90
                    }
        except Exception as e:
            return {
                "results": [],
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _search_duckduckgo(self, query: str, num_results: int) -> Dict:
        """Search using DuckDuckGo."""
        try:
            from duckduckgo_search import DDGS
            
            with DDGS() as ddgs:
                results = []
                for r in ddgs.text(query, max_results=num_results):
                    results.append({
                        "title": r.get("title"),
                        "url": r.get("href"),
                        "snippet": r.get("body")
                    })
                
                return {
                    "results": results,
                    "total_results": len(results),
                    "confidence": 0.85
                }
        except Exception as e:
            return {
                "results": [],
                "error": str(e),
                "confidence": 0.0
            }
