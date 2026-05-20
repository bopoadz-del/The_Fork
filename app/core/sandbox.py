"""RestrictedPython sandbox — Reasoning Engine Plan 3.

Executes untrusted, LLM-generated Python in a jailed namespace so the
reasoning engine can run code it did not write without risking the host
process. The sandbox:

* compiles code with ``RestrictedPython.compile_restricted`` (AST rewrite),
* exposes only a whitelist of safe builtins and importable modules,
* injects session state as ordinary variables,
* captures ``print`` output and the value of a designated result variable,
* always returns a structured :class:`SandboxResult` — sandboxed code can
  fail, but it can never crash or escape the caller.

This is `app/core/sandbox.py`. It is unrelated to the legacy
`app/blocks/sandbox.py` block.
"""

from __future__ import annotations

import builtins as _py_builtins
import copy
import warnings
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getiter
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.PrintCollector import PrintCollector

# --------------------------------------------------------------------------
# Policy: what sandboxed code may import and which builtins it may not call.
# --------------------------------------------------------------------------

#: Modules sandboxed code is allowed to ``import``. Everything else is denied.
ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "app.lib.pm_computations",
        "math",
        "statistics",
        "datetime",
        "json",
    }
)

#: Builtins that are never exposed, even if RestrictedPython would allow them.
#: ``open`` / ``eval`` / ``exec`` / ``__import__`` are already absent from
#: ``safe_builtins``; listing them documents intent and guards against future
#: RestrictedPython releases that might add them back.
BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint"}
)

#: Pure, side-effect-free builtins that ``RestrictedPython.safe_builtins``
#: conservatively omits but PM/formula code routinely needs (aggregates,
#: iteration helpers, container constructors). None of these can touch the
#: filesystem, network, or process — they are safe to expose.
_EXTRA_SAFE_BUILTINS: frozenset[str] = frozenset(
    {
        "sum", "min", "max", "all", "any", "enumerate", "map", "filter",
        "reversed", "list", "dict", "set", "frozenset", "iter", "next",
    }
)

#: Name of the variable the sandbox reads back as the structured result.
DEFAULT_RESULT_VAR = "result"


class SandboxError(Exception):
    """Raised for sandbox *setup* failures (bad config), never for errors
    inside sandboxed code — those are reported via :class:`SandboxResult`."""


class SandboxResult(BaseModel):
    """Structured outcome of one :func:`run_sandboxed` call.

    ``success`` is ``True`` only when the code compiled and ran without
    raising. On failure ``error`` holds a human-readable message and
    ``error_type`` the exception class name; ``stdout`` still contains
    whatever was printed before the failure.
    """

    success: bool
    stdout: str = ""
    result: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    #: Final values of injected/created variables (best effort, for debugging).
    namespace: Dict[str, Any] = Field(default_factory=dict)


def _make_guarded_import() -> Any:
    """Build an ``__import__`` replacement that honours :data:`ALLOWED_MODULES`.

    A submodule import (``import app.lib.pm_computations``) is allowed only if
    its full dotted path is whitelisted; ``from os import path`` is denied
    because ``os`` is not in the whitelist.
    """

    def _guarded_import(
        name: str,
        globals: Optional[Dict[str, Any]] = None,
        locals: Optional[Dict[str, Any]] = None,
        fromlist: tuple = (),
        level: int = 0,
    ) -> Any:
        if level != 0:
            raise ImportError("Relative imports are not permitted in the sandbox.")
        if name not in ALLOWED_MODULES:
            raise ImportError(
                f"Import of '{name}' is blocked by the sandbox. "
                f"Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}."
            )
        return __import__(name, globals, locals, fromlist, level)

    return _guarded_import


def _build_builtins() -> Dict[str, Any]:
    """Return the ``__builtins__`` mapping exposed to sandboxed code."""
    builtins: Dict[str, Any] = dict(safe_builtins)
    # Re-add pure aggregate/iteration builtins safe_builtins omits.
    for name in _EXTRA_SAFE_BUILTINS:
        builtins[name] = getattr(_py_builtins, name)
    # Strip anything explicitly blocked (defensive — most are absent already,
    # and this also guarantees nothing dangerous slipped in via the line above).
    for blocked in BLOCKED_BUILTINS:
        builtins.pop(blocked, None)
    # Provide a whitelisted importer so `import math` etc. still works.
    builtins["__import__"] = _make_guarded_import()
    return builtins


def _build_globals(state: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the exec globals: restricted builtins, RestrictedPython
    guards, and the injected session ``state``."""
    glb: Dict[str, Any] = {
        "__builtins__": _build_builtins(),
        # RestrictedPython rewrites code to call these helpers by name.
        "_print_": PrintCollector,
        "_getattr_": safer_getattr,
        "_getitem_": lambda obj, key: obj[key],
        "_getiter_": default_guarded_getiter,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_write_": lambda obj: obj,
    }
    # Inject session state as ordinary, mutable variables.
    glb.update(state)
    return glb


def run_sandboxed(
    code: str,
    state: Optional[Dict[str, Any]] = None,
    result_var: str = DEFAULT_RESULT_VAR,
) -> SandboxResult:
    """Execute ``code`` in the RestrictedPython jail and return the outcome.

    Args:
        code: Untrusted Python source to run.
        state: Variables injected into the namespace before execution
            (the session-state round-trip). Defaults to empty.
        result_var: Name of the variable read back into
            :attr:`SandboxResult.result` after a successful run. If the
            variable is never assigned, ``result`` stays ``None``.

    Returns:
        A :class:`SandboxResult`. This function never raises for errors
        *inside* the sandboxed code — compile errors, blocked imports,
        and runtime exceptions are all captured into the result.
    """
    # Deep-copy so sandboxed mutations of injected mutables (lists, dicts)
    # never leak back into the caller's session state.
    try:
        injected = copy.deepcopy(dict(state or {}))
    except Exception:
        # Un-copyable value — fall back to a shallow copy rather than fail.
        injected = dict(state or {})
    glb = _build_globals(injected)

    # --- compile (catches SyntaxError and RestrictedPython rejections) -----
    try:
        with warnings.catch_warnings():
            # RestrictedPython warns when `print` is used but `printed`
            # is never read — harmless here, we read the collector directly.
            warnings.simplefilter("ignore", SyntaxWarning)
            byte_code = compile_restricted(code, filename="<sandbox>", mode="exec")
    except SyntaxError as exc:
        return SandboxResult(
            success=False,
            error=f"Syntax error: {exc}",
            error_type="SyntaxError",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return SandboxResult(
            success=False,
            error=f"Code rejected by sandbox compiler: {exc}",
            error_type=type(exc).__name__,
        )

    # --- execute (catches every runtime failure) ---------------------------
    local_ns: Dict[str, Any] = {}
    try:
        exec(byte_code, glb, local_ns)
    except Exception as exc:
        stdout = _extract_stdout(local_ns)
        return SandboxResult(
            success=False,
            stdout=stdout,
            error=str(exc) or type(exc).__name__,
            error_type=type(exc).__name__,
            namespace=_safe_namespace(local_ns),
        )

    return SandboxResult(
        success=True,
        stdout=_extract_stdout(local_ns),
        result=local_ns.get(result_var),
        namespace=_safe_namespace(local_ns),
    )


def _extract_stdout(local_ns: Dict[str, Any]) -> str:
    """Pull captured text out of RestrictedPython's print collector.

    RestrictedPython rewrites ``print(...)`` to append to a ``_print``
    :class:`PrintCollector` in the local namespace; calling it (or reading
    ``.txt``) yields the accumulated output.
    """
    collector = local_ns.get("_print")
    if collector is None:
        return ""
    try:
        if callable(collector):
            return collector()
        txt = getattr(collector, "txt", None)
        if isinstance(txt, list):
            return "".join(txt)
        return str(txt or "")
    except Exception:  # pragma: no cover - defensive
        return ""


def _safe_namespace(local_ns: Dict[str, Any]) -> Dict[str, Any]:
    """Return the user-visible namespace, dropping RestrictedPython internals
    (the ``_print`` collector, dunder names) for clean inspection."""
    return {
        k: v
        for k, v in local_ns.items()
        if not k.startswith("_")
    }
