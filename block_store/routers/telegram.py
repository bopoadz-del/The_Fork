"""Telegram Bot webhook router"""
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram webhook updates."""
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    from app.blocks import BLOCK_REGISTRY
    cls = BLOCK_REGISTRY.get("telegram_bot")
    if not cls:
        raise HTTPException(503, "TelegramBotBlock not registered")

    bot = cls()
    result = await bot.process({"operation": "process_webhook", "update": update}, {})
    return {"ok": True, "result": result}


@router.get("/webhook/telegram/set")
async def set_telegram_webhook(url: str):
    """Set the Telegram webhook URL (admin utility)."""
    from app.blocks import BLOCK_REGISTRY
    cls = BLOCK_REGISTRY.get("telegram_bot")
    if not cls:
        raise HTTPException(503, "TelegramBotBlock not registered")

    bot = cls()
    result = await bot.process({"operation": "set_webhook", "url": url}, {})
    return result


@router.get("/webhook/telegram/info")
async def telegram_bot_info():
    """Get bot identity and webhook status."""
    from app.blocks import BLOCK_REGISTRY
    cls = BLOCK_REGISTRY.get("telegram_bot")
    if not cls:
        raise HTTPException(503, "TelegramBotBlock not registered")

    bot = cls()
    result = await bot.process({"operation": "get_me"}, {})
    return result
