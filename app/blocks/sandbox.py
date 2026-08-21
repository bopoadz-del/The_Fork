"""Sandbox Block - Code execution isolation and security.

POSIX-only: the `resource` and `signal.SIGXCPU` primitives this block uses
to enforce memory / CPU limits don't exist on Windows. We import them
conditionally so the block stays loadable on Windows (it will raise an
honest error when actually invoked there, rather than failing at import
time and blocking the whole registry).
"""
from app.core.subprocess_env import scrubbed_env
from app.core.universal_base import UniversalBlock
from typing import Dict, Any, Callable, Optional
import asyncio
import tempfile
import os
import signal
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)

try:
    import resource  # POSIX only
    _RESOURCE_AVAILABLE = True
except ImportError:  # pragma: no cover — Windows path
    resource = None  # type: ignore[assignment]
    _RESOURCE_AVAILABLE = False


class SandboxLevel(Enum):
    """Security sandbox levels"""
    NONE = "none"           # No sandboxing
    PERMISSIVE = "permissive"  # Log only, don't block
    STRICT = "strict"       # Block dangerous operations
    ISOLATED = "isolated"   # Full process isolation


@dataclass
class SandboxPolicy:
    """Sandbox policy configuration"""
    max_memory_mb: int = 512
    max_cpu_time: int = 5  # seconds
    max_file_size_mb: int = 10
    network_allowed: bool = False
    filesystem_readonly: bool = True
    allowed_modules: list = None
    blocked_builtins: list = None
    
    def __post_init__(self):
        if self.allowed_modules is None:
            self.allowed_modules = ["math", "random", "datetime", "json", "re"]
        if self.blocked_builtins is None:
            self.blocked_builtins = ["open", "exec", "eval", "compile"]


class TimeoutException(Exception):
    pass


# Wall-clock slack on top of SandboxPolicy.max_cpu_time for JS/bash subprocess
# spawn. max_cpu_time is the user-code budget; process creation on a loaded
# GitHub Actions runner (full pytest + coverage) is extra. Treating that
# startup as "Execution timeout" failed
# tests/test_sandbox_block.py::test_execute_javascript_simple on PR #397
# (CI run 32449705289, virgin profile) with
# ``{'error': 'Execution timeout', 'killed': True}`` for ``console.log(2+3)``.
_SUBPROCESS_SPAWN_GRACE_S = 25.0


def _subprocess_wall_s(policy: SandboxPolicy) -> float:
    """Deadline for spawn + communicate. Always longer than max_cpu_time."""
    return float(policy.max_cpu_time) + _SUBPROCESS_SPAWN_GRACE_S
