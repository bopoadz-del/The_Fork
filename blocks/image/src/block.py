from blocks.base import LegoBlock
from typing import Dict, Any

class ImageBlock(LegoBlock):
    """Image Generation & Analysis"""
    name = "image"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Utility layer
    tags = ["image", "vision", "generation", "utility"]
    default_config = {
        "provider": "openai",
        "model": "dall-e-3",
        "size": "1024x1024"
    }
    
    PROVIDERS = {
        "openai": {"url": "https://api.openai.com/v1/images", "model": "dall-e-3"},
        "stability": {"url": "https://api.stability.ai/v2beta", "model": "stable-diffusion-xl"},
        "replicate": {"url": "https://api.replicate.com/v1", "model": "black-forest-labs/flux"},
        "local_sdxl": {"url": "local", "model": "stabilityai/stable-diffusion-xl-base"}
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.api_key = config.get("openai_key") or config.get("stability_key")
        self.use_local = config.get("use_local", False)
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "generate":
            return await self._generate(input_data)
        elif action == "analyze":
            return await self._analyze(input_data)
        elif action == "caption":
            return await self._caption(input_data)
        return {"error": "Unknown action"}
    
    async def _generate(self, data: Dict) -> Dict:
        prompt = data.get("prompt")
        provider = data.get("provider", "openai" if not self.use_local else "local_sdxl")
        
        if provider == "local_sdxl":
            try:
                from diffusers import StableDiffusionPipeline
                import torch
                
                pipe = StableDiffusionPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32
                ).to("cuda" if torch.cuda.is_available() else "cpu")
                
                image = pipe(prompt, num_inference_steps=30).images[0]
                from io import BytesIO
                buf = BytesIO()
                image.save(buf, format="PNG")
                return {"image": buf.getvalue(), "format": "png", "provider": "local_sdxl"}
            except ImportError:
                return {"error": "diffusers not installed"}
        
        elif provider == "openai":
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"prompt": prompt, "model": "dall-e-3", "size": "1024x1024"}
                ) as resp:
                    result = await resp.json()
                    return {"url": result["data"][0]["url"], "provider": "openai"}
        
        return {"error": f"Provider {provider} not supported"}
    
    async def _analyze(self, data: Dict) -> Dict:
        image_bytes = data.get("image")
        prompt = data.get("prompt", "Describe this image in detail")
        import aiohttp
        import base64
        
        b64_image = base64.b64encode(image_bytes).decode()
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                        ]
                    }]
                }
            ) as resp:
                result = await resp.json()
                return {"description": result["choices"][0]["message"]["content"], "provider": "gpt4v"}
    
    async def _caption(self, data: Dict) -> Dict:
        return await self._analyze(data)
    
    def health(self) -> Dict:
        h = super().health()
        h["local_mode"] = self.use_local
        return h
