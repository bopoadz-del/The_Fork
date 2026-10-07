"""Driver mode (F-DRIVER Phase B), behind ``DRIVER_MODE`` -- off by default.

In driver mode the model drives the turn. It is given the project's profile,
the conversation and the hats' descriptions; it chooses a hat itself
(``select_hat``); it calls tools with ``tool_choice`` auto. No code chooses a
route or forces a tool, and nothing is injected automatically -- knowledge
is reached through tools. Steps and model are configuration.
"""
from __future__ import annotations

import os

_TRUTHY = ("1", "true", "yes", "on")


def enabled() -> bool:
    return (os.getenv("DRIVER_MODE") or "").strip().lower() in _TRUTHY


def max_steps() -> int:
    try:
        return max(1, int(os.getenv("DRIVER_MAX_STEPS") or "6"))
    except ValueError:
        return 6


def model() -> str:
    """Model for driver turns; empty means the provider's default."""
    return (os.getenv("DRIVER_MODEL") or "").strip()
