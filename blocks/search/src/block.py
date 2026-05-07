"""Search Block - WORKING web search"""
from blocks.base import LegoBlock
from typing import Dict, Any
import urllib.parse

class SearchBlock(LegoBlock):
    """Web Search - FREE DuckDuckGo (no API key) + optional API providers"""
    name = "search"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Integration layer
    tags = ["search", "web", "integration", "free"]
    default_config = {
        "default_provider": "duckduckgo",  # FREE - no API key needed!
        "brave_key": None,      # Optional: brave.com/search/api
        "serper_key": None,     # Optional: serper.dev
        "tavily_key": None      # Optional: tavily.com
    }
    
    PROVIDERS = {
        "serper": {"url": "https://google.serper.dev/search", "type": "google"},
        "tavily": {"url": "https://api.tavily.com/search", "type": "ai_search"},
        "brave": {"url": "https://api.search.brave.com/res/v1/web/search", "type": "privacy"},
        "duckduckgo": {"url": "html", "type": "scrape"}  # Free
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.api_key = config.get("serper_key") or config.get("tavily_key") or config.get("brave_key")
        self.brave_key = config.get("brave_key")
        self.default_provider = config.get("default", "duckduckgo")
    
    async def initialize(self) -> bool:
        """Initialize search block"""
        print(f"🔎 Search Block initialized")
        print(f"   Default: {self.default_provider} (FREE - no API key!)")
        print(f"   Providers: {', '.join(self.PROVIDERS.keys())}")
        print(f"   Optional API Keys: serper={'yes' if self.config.get('serper_key') else 'no'}, "
              f"tavily={'yes' if self.config.get('tavily_key') else 'no'}, "
              f"brave={'yes' if self.brave_key else 'no'}")
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "search":
            return await self._web_search(input_data)
        elif action == "news":
            return await self._news_search(input_data)
        elif action == "images":
            return await self._image_search(input_data)
        return {"error": "Unknown action"}
    
    async def _web_search(self, data: Dict) -> Dict:
        """Web search with multiple providers"""
        query = data.get("query")
        provider = data.get("provider", self.default_provider)
        num_results = data.get("num", 10)
        
        if provider == "duckduckgo":
            return await self._duckduckgo_search(query, num_results)
        elif provider == "serper":
            return await self._serper_search(query, num_results)
        elif provider == "tavily":
            return await self._tavily_search(query, num_results)
        elif provider == "brave":
            return await self._brave_search(query, num_results)
        
        return {"error": f"Unknown provider: {provider}"}
    
    async def _duckduckgo_search(self, query: str, num: int) -> Dict:
        """Scrape DuckDuckGo (free, no API key)"""
        try:
            import aiohttp
            from bs4 import BeautifulSoup
            
            # HTML version (more reliable than JS version)
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        return {"error": f"DuckDuckGo returned {resp.status}"}
                    
                    html = await resp.text()
                    
                    # Parse results
                    soup = BeautifulSoup(html, 'html.parser')
                    results = []
                    
                    for result in soup.select('.result'):
                        title_elem = result.find('a', class_='result__a')
                        snippet_elem = result.find('a', class_='result__snippet')
                        
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            link = title_elem.get('href', '')
                            
                            # Clean up link
                            if link.startswith('/l/?'):
                                # Extract actual URL from redirect
                                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                                link = parsed.get('uddg', [''])[0]
                            
                            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                            
                            results.append({
                                "title": title,
                                "link": link,
                                "snippet": snippet
                            })
                            
                            if len(results) >= num:
                                break
                    
                    return {
                        "results": results,
                        "count": len(results),
                        "provider": "duckduckgo",
                        "query": query
                    }
                    
        except ImportError as e:
            return {"error": f"Missing dependency: {str(e)}. Run: pip install aiohttp beautifulsoup4"}
        except Exception as e:
            return {"error": f"DuckDuckGo search failed: {str(e)}"}
    
    async def _serper_search(self, query: str, num: int) -> Dict:
        """Serper.dev Google Search API"""
        if not self.api_key:
            return {"error": "Serper API key not configured"}
        
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.PROVIDERS["serper"]["url"],
                    headers={
                        "X-API-KEY": self.api_key,
                        "Content-Type": "application/json"
                    },
                    json={"q": query, "num": num}
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"error": f"Serper API error: {error}"}
                    
                    result = await resp.json()
                    
                    return {
                        "results": [
                            {
                                "title": r.get("title"),
                                "link": r.get("link"),
                                "snippet": r.get("snippet"),
                                "date": r.get("date"),
                                "position": r.get("position")
                            }
                            for r in result.get("organic", [])
                        ],
                        "knowledge_graph": result.get("knowledgeGraph"),
                        "provider": "serper",
                        "query": query
                    }
                    
        except ImportError:
            return {"error": "aiohttp not installed"}
        except Exception as e:
            return {"error": f"Serper search failed: {str(e)}"}
    
    async def _tavily_search(self, query: str, num: int) -> Dict:
        """Tavily AI Search API"""
        if not self.api_key:
            return {"error": "Tavily API key not configured"}
        
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.PROVIDERS["tavily"]["url"],
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "max_results": num,
                        "include_answer": True
                    }
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"error": f"Tavily API error: {error}"}
                    
                    result = await resp.json()
                    
                    return {
                        "results": result.get("results", []),
                        "answer": result.get("answer"),
                        "query": query,
                        "provider": "tavily"
                    }
                    
        except ImportError:
            return {"error": "aiohttp not installed"}
        except Exception as e:
            return {"error": f"Tavily search failed: {str(e)}"}
    
    async def _brave_search(self, query: str, num: int) -> Dict:
        """Brave Search API - Privacy-focused, no tracking"""
        if not self.brave_key:
            return {"error": "Brave API key not configured. Get one at: https://brave.com/search/api/"}
        
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.PROVIDERS["brave"]["url"],
                    headers={
                        "X-Subscription-Token": self.brave_key,
                        "Accept": "application/json"
                    },
                    params={
                        "q": query,
                        "count": min(num, 20),  # Brave max is 20
                        "offset": 0,
                        "mkt": "en-US"
                    }
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"error": f"Brave API error: {resp.status} - {error}"}
                    
                    result = await resp.json()
                    
                    # Parse Brave results
                    web_results = result.get("web", {}).get("results", [])
                    
                    return {
                        "results": [
                            {
                                "title": r.get("title"),
                                "link": r.get("url"),
                                "snippet": r.get("description"),
                                "age": r.get("age"),
                                "family_friendly": r.get("family_friendly", True)
                            }
                            for r in web_results
                        ],
                        "query": query,
                        "provider": "brave",
                        "total": len(web_results),
                        "features": {
                            "privacy": True,
                            "no_tracking": True,
                            "independent_index": True
                        }
                    }
                    
        except ImportError:
            return {"error": "aiohttp not installed"}
        except Exception as e:
            return {"error": f"Brave search failed: {str(e)}"}
    
    async def _news_search(self, data: Dict) -> Dict:
        """Search news specifically (uses DuckDuckGo - free, no API key)"""
        query = data.get("query")
        # Add news filter and use DuckDuckGo (free)
        news_query = f"{query} news"
        return await self._duckduckgo_search(news_query, data.get("num", 10))
    
    async def _image_search(self, data: Dict) -> Dict:
        """Image search (uses DuckDuckGo Images - free, no API key)"""
        query = data.get("query")
        num = data.get("num", 10)
        
        # Try DuckDuckGo Images first (free, no API key)
        try:
            import aiohttp
            from bs4 import BeautifulSoup
            
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://duckduckgo.com/?q={encoded_query}&iax=images&ia=images"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Extract image results
                        images = []
                        for img in soup.select('.tile--img__img')[:num]:
                            src = img.get('src', '')
                            if src.startswith('//'):
                                src = 'https:' + src
                            images.append({
                                "title": img.get('alt', ''),
                                "link": src,
                                "source": "duckduckgo"
                            })
                        
                        if images:
                            return {
                                "images": images,
                                "provider": "duckduckgo",
                                "query": query,
                                "count": len(images)
                            }
        except Exception:
            pass  # Fall through to API provider
        
        # Fallback to Serper if configured
        if self.api_key:
            try:
                import aiohttp
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://google.serper.dev/images",
                        headers={
                            "X-API-KEY": self.api_key,
                            "Content-Type": "application/json"
                        },
                        json={"q": query, "num": num}
                    ) as resp:
                        result = await resp.json()
                        
                        return {
                            "images": [
                                {
                                    "title": img.get("title"),
                                    "link": img.get("imageUrl"),
                                    "source": img.get("source")
                                }
                                for img in result.get("images", [])
                            ],
                            "provider": "serper"
                        }
                        
            except Exception as e:
                return {"error": f"Image search failed: {str(e)}"}
        
        return {"error": "Image search: DuckDuckGo failed and no API key configured"}
    
    def health(self) -> Dict:
        h = super().health()
        h["providers"] = list(self.PROVIDERS.keys())
        h["default"] = self.default_provider
        h["api_key_configured"] = self.api_key is not None
        h["brave_key_configured"] = self.brave_key is not None
        return h
