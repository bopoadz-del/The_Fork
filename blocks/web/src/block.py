from blocks.base import LegoBlock
from typing import Dict, Any

class WebBlock(LegoBlock):
    """Web Scraping & Browsing"""
    name = "web"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Utility layer
    tags = ["web", "scraping", "browser", "utility"]
    default_config = {
        "timeout": 30,
        "user_agent": "CerebrumBot/1.0",
        "respect_robots": True
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.use_playwright = config.get("use_playwright", False)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        }
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "scrape":
            return await self._scrape(input_data)
        elif action == "screenshot":
            return await self._screenshot(input_data)
        elif action == "extract_links":
            return await self._extract_links(input_data)
        return {"error": "Unknown action"}
    
    async def _scrape(self, data: Dict) -> Dict:
        url = data.get("url")
        selector = data.get("selector")
        render_js = data.get("render_js", False)
        
        if render_js and self.use_playwright:
            try:
                from playwright.async_api import async_playwright
                
                async with async_playwright() as p:
                    browser = await p.chromium.launch()
                    page = await browser.new_page()
                    await page.goto(url)
                    
                    if selector:
                        content = await page.inner_text(selector)
                    else:
                        content = await page.content()
                    
                    await browser.close()
                    return {"content": content, "url": url, "method": "playwright"}
            except ImportError:
                return {"error": "playwright not installed"}
        
        else:
            import aiohttp
            from bs4 import BeautifulSoup
            
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url) as resp:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    if selector:
                        elements = soup.select(selector)
                        content = "\n".join([e.get_text() for e in elements])
                    else:
                        content = soup.get_text()
                    
                    return {
                        "content": content[:10000],
                        "title": soup.title.string if soup.title else None,
                        "url": url,
                        "method": "static"
                    }
    
    async def _screenshot(self, data: Dict) -> Dict:
        url = data.get("url")
        full_page = data.get("full_page", False)
        
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(url)
                
                screenshot = await page.screenshot(full_page=full_page, type="png")
                await browser.close()
                
                return {"screenshot": screenshot, "format": "png", "url": url}
        except ImportError:
            return {"error": "playwright not installed"}
    
    async def _extract_links(self, data: Dict) -> Dict:
        url = data.get("url")
        import aiohttp
        from bs4 import BeautifulSoup
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                links = [
                    {
                        "text": a.get_text(strip=True),
                        "href": a.get('href'),
                        "external": a.get('href', '').startswith('http')
                    }
                    for a in soup.find_all('a', href=True)
                ]
                
                return {
                    "links": links[:100],
                    "count": len(links),
                    "url": url
                }
    
    def health(self) -> Dict:
        h = super().health()
        h["playwright"] = self.use_playwright
        return h
