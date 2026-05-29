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
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover — zoneinfo is stdlib from 3.9
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment, misc]


logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None


def _enabled() -> bool:
    return os.getenv("HYDRATION_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _hour_utc() -> int:
    """Read the configured hour. The env var name still mentions UTC for
    backwards compatibility, but the hour is interpreted in ``_tz()``'s
    timezone — UTC by default, an IANA zone when ``HYDRATION_TZ`` is set."""
    try:
        h = int(os.getenv("HYDRATION_HOUR_UTC", "1"))
    except ValueError:
        h = 1
    return max(0, min(h, 23))


def _tz() -> tzinfo:
    """Resolve the timezone the hydration hour is interpreted in.

    Set ``HYDRATION_TZ`` to an IANA zone name (e.g. ``Asia/Dubai``,
    ``America/New_York``) to run the nightly pass during the local quiet
    window for a construction site. Defaults to UTC. An invalid zone name
    logs a warning and falls back to UTC rather than crashing the loop.
    """
    name = (os.getenv("HYDRATION_TZ") or "").strip()
    if not name or name.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        logger.warning("HYDRATION_TZ=%s requested but zoneinfo unavailable; using UTC", name)
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("HYDRATION_TZ=%s is not a valid IANA zone; using UTC", name)
        return timezone.utc


def _seconds_until_next(hour: int, tz: Optional[tzinfo] = None) -> float:
    """Seconds from now until the next ``hour:00`` in ``tz`` (default UTC).

    Computing the target in the destination timezone (not UTC) is what makes
    "1 AM Dubai" actually fire at 1 AM Dubai across DST transitions and
    UTC-offset zones — converting once at fire-time is wrong because the next
    occurrence in local time may be more or less than 24h away in UTC.
    """
    tz = tz or timezone.utc
    now_local = datetime.now(tz)
    target_local = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target_local <= now_local:
        target_local = target_local + timedelta(days=1)
    delta = target_local - now_local
    return max(1.0, delta.total_seconds())


async def _run_one_pass() -> None:
    """Invoke the learning_engine's ``hydrate`` operation, then trigger an
    incremental ``train_router`` if new routing_decisions have accumulated
    since the last train. Imported lazily so the scheduler module can be
    imported even if the block failed to register, and so the test suite
    can stub the registry.

    Auto-retrain is best-effort: failures are logged but never abort the
    scheduler loop. Skips entirely when no new corrections/decisions have
    landed (no point re-fitting on identical data)."""
    from app.blocks import BLOCK_REGISTRY

    cls = BLOCK_REGISTRY.get("learning_engine")
    if cls is None:
        logger.warning("hydration scheduler: learning_engine block not registered, skipping")
        return
    block = cls()
    try:
        envelope = await block.execute({"operation": "hydrate"}, {})
        # The execute() wrapper nests the operation's return in `result`.
        inner = envelope.get("result") if isinstance(envelope, dict) else {}
        logger.info(
            "hydration pass complete: projects=%s files_indexed=%s",
            (inner or {}).get("projects_processed"),
            (inner or {}).get("files_indexed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("hydration pass failed: %s", exc)

    # ── Auto-retrain hook (W4 follow-up) ──────────────────────────────
    # The classifier learns from runtime routing_decisions; without a
    # retrain trigger those rows accumulate on disk and never reach the
    # model. Piggy-backing on the nightly hydration scheduler gives us a
    # natural cadence (once per day) without standing up a second cron.
    try:
        await _maybe_retrain_router(block)
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto-retrain hook failed: %s", exc)


async def _maybe_retrain_router(block) -> None:
    """Retrain the router classifier when new routing_decisions have
    accumulated since the last training run. Skips otherwise.

    The "did we accumulate anything?" check compares the total pattern
    count to the count at last train time (persisted under
    ``models.router.patterns_seen_at_train``). When equal, we skip; the
    model is already current. Cheap signal — no need to actually count
    corrections specifically, the classifier benefits from any new
    routing_decisions row.

    Uses ``prefer_corrected=true`` once corrected rows clear a threshold
    (currently 40, matching ``_MIN_TOTAL_SAMPLES``) — at that point the
    model can stand on user-validated labels alone.
    """
    state = block._state
    patterns_total = 0
    corrected_total = 0
    for proj_buckets in state.get("patterns", {}).values():
        for obs in proj_buckets.get("routing_decisions", []):
            patterns_total += 1
            try:
                import json as _json
                payload = _json.loads(obs.get("observation", "") or "")
                if payload.get("corrected"):
                    corrected_total += 1
            except Exception:
                pass

    last_count = (state.get("models", {}).get("router") or {}).get(
        "patterns_seen_at_train", 0
    )
    if patterns_total <= last_count:
        logger.info(
            "auto-retrain skipped: no new routing_decisions (current=%d, last_train=%d)",
            patterns_total, last_count,
        )
        return

    # Use prefer_corrected once we have enough corrections to stand alone
    from app.core.learning.router import _MIN_TOTAL_SAMPLES
    prefer_corrected = corrected_total >= _MIN_TOTAL_SAMPLES

    logger.info(
        "auto-retrain triggered: %d new routing_decisions, %d corrections (prefer_corrected=%s)",
        patterns_total - last_count, corrected_total, prefer_corrected,
    )
    envelope = await block.execute(
        {"operation": "train_router", "prefer_corrected": prefer_corrected},
        {},
    )
    inner = envelope.get("result", {}) if isinstance(envelope, dict) else {}
    status = inner.get("status")
    if status == "success":
        # Stamp the snapshot count so the next pass knows what we trained on
        block._state.setdefault("models", {}).setdefault("router", {})
        block._state["models"]["router"]["patterns_seen_at_train"] = patterns_total
        block._save_state()
        logger.info(
            "auto-retrain complete: status=success accuracy=%s samples_used=%s",
            inner.get("accuracy"), inner.get("samples_used"),
        )
    else:
        logger.warning(
            "auto-retrain returned non-success: status=%s reason=%s",
            status, inner.get("remediation") or inner.get("error") or "n/a",
        )


async def _loop() -> None:
    hour = _hour_utc()
    tz = _tz()
    tz_label = getattr(tz, "key", None) or str(tz)
    logger.info("hydration scheduler started, target = %02d:00 %s", hour, tz_label)
    while True:
        try:
            delay = _seconds_until_next(hour, tz)
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
