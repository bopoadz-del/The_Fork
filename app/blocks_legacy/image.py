"""Image Block - Image analysis and generation."""

import os
import io
import base64
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
from PIL import Image
import aiohttp


class ImageBlock(BaseBlock):
    """Image analysis (description, classification) and generation."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="image",
            version="1.0",
            description="Image analysis and generation",
            requires_api_key=True,
            supported_inputs=["image", "prompt"],
            supported_outputs=["description", "image"]
        ,
            layer=3,
            tags=["domain", "vision", "image"]))
        self._openai_available = self._check_openai()
    
    def _check_openai(self) -> bool:
        try:
            import openai
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process image (analyze or generate)."""
        params = params or {}
        operation = params.get("operation", "auto")
        
        if operation == "auto":
            operation = self._detect_operation(input_data)
        
        if operation == "analyze":
            return await self._analyze_image(input_data, params)
        elif operation == "generate":
            return await self._generate_image(input_data, params)
        else:
            return {
                "error": "Unknown operation. Use 'analyze' or 'generate'.",
                "confidence": 0.0
            }
    
    def _detect_operation(self, input_data: Any) -> str:
        """Auto-detect operation type."""
        if isinstance(input_data, str) and not os.path.exists(input_data):
            return "generate"
        if isinstance(input_data, dict) and "prompt" in input_data:
            return "generate"
        return "analyze"
    
    async def _analyze_image(self, input_data: Any, params: Dict) -> Dict:
        """Analyze/describe an image."""
        provider = params.get("provider", "openai")
        prompt = params.get("prompt", "Describe this image in detail.")
        
        image = self._load_image(input_data)
        
        result = {
            "operation": "analyze",
            "image_size": image.size,
            "image_mode": image.mode,
        }
        
        if provider == "openai" and self._openai_available:
            import openai
            
            client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            try:
                # Convert image to base64
                buffered = io.BytesIO()
                image.save(buffered, format="PNG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                
                response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=500
                )
                
                result["description"] = response.choices[0].message.content
                result["provider"] = "openai"
                result["confidence"] = 0.90
                
            except Exception as e:
                result["error"] = str(e)
                result["confidence"] = 0.0
        else:
            # Fallback: return basic image info
            result["description"] = f"Image analysis not available. Image size: {image.size}"
            result["confidence"] = 0.3
        
        return result
    
    async def _generate_image(self, input_data: Any, params: Dict) -> Dict:
        """Generate an image from text."""
        provider = params.get("provider", "openai")
        size = params.get("size", "1024x1024")
        quality = params.get("quality", "standard")
        
        prompt = self._get_prompt(input_data)
        
        result = {
            "operation": "generate",
            "prompt": prompt,
            "size": size,
        }
        
        if provider == "openai" and self._openai_available:
            import openai
            
            client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            try:
                response = await client.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    size=size,
                    quality=quality,
                    n=1
                )
                
                image_url = response.data[0].url
                
                # Download the image
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        image_data = await resp.read()
                
                result["image_base64"] = base64.b64encode(image_data).decode("utf-8")
                result["image_url"] = image_url
                result["revised_prompt"] = response.data[0].revised_prompt
                result["provider"] = "openai"
                result["confidence"] = 0.95
                
            except Exception as e:
                result["error"] = str(e)
                result["confidence"] = 0.0
        elif provider == "mock":
            # Generate a simple colored placeholder
            img = Image.new("RGB", (512, 512), color=(100, 150, 200))
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            result["image_base64"] = base64.b64encode(buffered.getvalue()).decode()
            result["format"] = "png"
            result["provider"] = "mock"
            result["confidence"] = 1.0
        else:
            result["error"] = "Image generation not available"
            result["confidence"] = 0.0
        
        return result
    
    def _load_image(self, input_data: Any) -> Image.Image:
        """Load image from various input formats."""
        if isinstance(input_data, Image.Image):
            return input_data
        if isinstance(input_data, dict):
            if "image" in input_data:
                return input_data["image"]
            if "image_base64" in input_data:
                img_data = base64.b64decode(input_data["image_base64"])
                return Image.open(io.BytesIO(img_data))
            if "image_path" in input_data:
                return Image.open(input_data["image_path"])
            if "source_id" in input_data:
                return Image.open(f"/app/data/{input_data['source_id']}")
        if isinstance(input_data, str) and os.path.exists(input_data):
            return Image.open(input_data)
        raise ValueError("Invalid image input")
    
    def _get_prompt(self, input_data: Any) -> str:
        """Extract prompt from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "prompt" in input_data:
                return input_data["prompt"]
            if "text" in input_data:
                return input_data["text"]
        raise ValueError("Invalid prompt input")
