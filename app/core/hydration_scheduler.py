"""Hydration scheduler — single asyncio background task that fires the
hydration block once per day at the configured hour (default 01:00 UTC,
i.e. inside the 1am-3am quiet window).

Lifecycle is owned by ``app/main.py``'s lifespan context manager: ``start()``
is called on app boot, ``stop()`` on shutdown. The task is a guarded infinite
loop — exceptions inside one run are logged but never bubble out, so a bad
night never kills the scheduler.

Disable for tests/dev by setting ``HYDRATION_ENABLED=false`` in the env.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional


logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None


def _enabled() -> bool:
    return os.getenv("HYDRATION_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _hour_utc() -> int:
    try:
        h = int(os.getenv("HYDRATION_HOUR_UTC", "1"))
    except ValueError:
        h = 1
    return max(0, min(h, 23))


def _seconds_until_next(hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def _run_one_pass() -> None:
    """Invoke the hydration block once. Imported lazily so the scheduler
    module can be imported even if the block failed to register."""
    from app.blocks import BLOCK_REGISTRY

    cls = BLOCK_REGISTRY.get("hydration")
    if cls is None:
        logger.warning("hydration scheduler: hydration block not registered, skipping")
        return
    block = cls()
    try:
        result = await block.execute({"operation": "run"}, {})
        logger.info(
            "hydration pass complete: projects=%s files_indexed=%s",
            result.get("projects_processed"),
            result.get("files_indexed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("hydration pass failed: %s", exc)


async def _loop() -> None:
    hour = _hour_utc()
    logger.info("hydration scheduler started, target hour = %02d:00 UTC", hour)
    while True:
        try:
            delay = _seconds_until_next(hour)
            logger.info("hydration: next run in %d seconds", int(delay))
            await asyncio.sleep(delay)
            await _run_one_pass()
        except asyncio.CancelledError:
            logger.info("hydration scheduler stopping")
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            logger.exception("hydration scheduler loop error: %s", exc)
            # Back off briefly so a tight-loop failure mode can't burn CPU.
            await asyncio.sleep(60)


def start() -> None:
    """Spawn the background task. No-op if disabled or already running."""
    global _task
    if not _enabled():
        logger.info("hydration scheduler disabled via HYDRATION_ENABLED=false")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="hydration-scheduler")


async def stop() -> None:
    """Cancel the background task. Safe to call when not running."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
