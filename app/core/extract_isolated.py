"""Run document extraction in a child process with a hard memory ceiling.

WHY THIS EXISTS (measured, 2026-08-20 15:46-15:53 UTC on the live box):

    time     memory_MB   cpu_cores
    15:40       668.0       0.002   <- idle baseline
    15:44       788.2       0.049   <- chat turns only
    15:47      3641.6       1.198   <- ONE 4.4 MB PDF being indexed
    15:52      3892.6       1.761   <- peak, 95% of the 4 GB instance
    15:53      2581.0       1.700   <- SIGKILL
    15:55       591.8       0.138   <- fresh process

A single 4.4 MB PDF producing 376 chunks took the instance from 790 MB to
3.6 GB. The extracted TEXT was ~2 MB -- the memory is inside the PDF
libraries, not the output. ``_extract_pdf`` says as much about pdfplumber:
"it loads the whole PDF and is the OOM hazard", but only skips it for files
above ``_PDF_OCR_MAX_SIZE_MB``, and 4.4 MB is far below that.

The kernel OOM killer sends SIGKILL. Python raises nothing, ``except`` blocks
do not run, Sentry never fires -- so this failure is invisible in application
logs and shows up only as a 502 on whatever unrelated chat turn happened to be
in flight. With ``UVICORN_WORKERS`` unset the box runs ONE uvicorn worker, so
every concurrent request dies with it.

Bounding the file size cannot fix this: a 4.4 MB text PDF blew up while a
200 MB scan would not. What can be bounded is the extraction itself. Here it
runs in a forked child under ``RLIMIT_AS``, which turns an instance-killing
SIGKILL into a MemoryError inside a process nobody else depends on. The parent
returns empty text and a reason; the document lands at 0 chunks with a
diagnosis instead of taking the service down.

Isolation is skipped where fork is unavailable (Windows dev boxes), where it
is switched off, or when already inside a child -- extraction then runs
in-process exactly as before.

SIDE EFFECTS DO NOT PROPAGATE. A forked child gets a copy of memory, so
anything extraction does other than RETURN a value is lost when the child
exits: counters, caches, module-level state, monkeypatched spies. Only the
``(text, meta)`` tuple crosses back. CI caught this the first time the fork
path actually ran -- ``test_extract_pdf_ocr_page_cap_bounds_memory`` counts
``_ocr_pdf_page`` calls in a closure and saw 0, because the counting happened
in the child. Anything a caller needs to observe must be returned in ``meta``.

That property is also why isolation is gated on size below: forking is not
free, and a document too small to threaten the box should not pay for it.

KNOWN HAZARD, deliberately accepted: forking from a threaded server can wedge
the child if another thread held a lock (logging, allocator) at fork time. The
child only extracts and exits, so it never contends for the parent's locks
itself, and ``_TIMEOUT_S`` bounds the damage either way -- a wedged child is
reaped and reported as a timeout rather than hanging the request forever. The
alternative, spawn, re-imports the whole application per document and costs
far more than it saves.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Address-space ceiling for the child. Sized from the measurements above:
# baseline ~670 MB + a live chat turn ~190 MB leaves comfortable room for a
# 1.5 GB child on the 4 GB instance (~2.4 GB peak, 58%). Raising this past
# roughly 2.5 GB gives back the crash it prevents.
_MEM_MB = int(os.getenv("DOC_EXTRACT_MEM_MB", "1536"))

# A wedged extraction must not pin a worker forever. Real extractions on this
# corpus run 30-200s; OCR of a long scan is the slow end.
_TIMEOUT_S = float(os.getenv("DOC_EXTRACT_TIMEOUT_S", "600"))

_ENABLED = os.getenv("DOC_EXTRACT_ISOLATE", "1").strip().lower() not in {
    "0", "false", "no", "off",
}

# Below this, extract in-process. The measured blow-up was a 4.4 MB PDF; a
# document smaller than this cannot plausibly reach the 1.5 GB child ceiling,
# and running it in-process keeps extraction's side effects observable (see
# above) and avoids a fork per trivial file.
_MIN_MB = float(os.getenv("DOC_EXTRACT_ISOLATE_MIN_MB", "1"))

# Set in the child so a nested extraction (archive members recurse through
# _extract_with_meta) does not fork again per member.
_IN_CHILD_ENV = "DOC_EXTRACT_IN_CHILD"


def worth_isolating(file_path: str) -> bool:
    """True when this document is big enough to be worth a forked child.

    An unreadable/missing path returns False: extraction will fail anyway, and
    it should fail in-process where the error is visible rather than being
    reported as a child crash.
    """
    if not isolation_available():
        return False
    try:
        return (os.path.getsize(file_path) / (1024 * 1024)) >= _MIN_MB
    except OSError:
        return False


def isolation_available() -> bool:
    """True when a forking child with RLIMIT_AS can actually be used.

    Requires POSIX fork and the ``resource`` module. Windows spawn would
    re-import the whole app per document, which costs far more than it saves.
    """
    if not _ENABLED:
        return False
    if os.getenv(_IN_CHILD_ENV):
        return False
    if os.name != "posix":
        return False
    try:
        import multiprocessing
        import resource  # noqa: F401
    except Exception:
        return False
    return "fork" in multiprocessing.get_all_start_methods()


def _safe_send(conn, payload) -> bool:
    """Best-effort send from the child. False when the pipe is already gone.

    Not a silent handler: a failed send is genuinely unrecoverable here -- the
    parent has stopped listening, and the child is about to _exit -- but it
    must neither raise out of the finally chain nor be an `except: pass`,
    which the S110 gate in lint.yml blocks for good reason.
    """
    try:
        conn.send(payload)
        return True
    except Exception as exc:  # noqa: BLE001 - the child must not raise
        logger.debug("child could not send %r: %s", payload[0], exc)
        return False


def _safe_close(conn) -> None:
    """Close the pipe, tolerating a parent that already hung up."""
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("child could not close its pipe: %s", exc)


def _parent_virtual_bytes() -> int | None:
    """The parent's CURRENT virtual size, or None if it cannot be read."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read /proc/self/status VmSize: %s", exc)
    return None


def child_address_space_limit(budget_bytes: int) -> int | None:
    """Absolute RLIMIT_AS for the child: parent's virtual size + budget.

    RLIMIT_AS caps VIRTUAL address space, and fork() hands the child a copy of
    the parent's entire mapping. With torch and the embedding model resident
    the parent's VmSize is already multiple GB, so an ABSOLUTE ceiling of
    1536 MB was breached the instant the child started: the first allocation
    raised MemoryError, `_extract_pdf` swallowed it as an empty document, and
    every file >= 1 MB indexed as ZERO_CHUNK with nothing logged. That shipped.

    Budgeting on top of the parent's current size states the thing actually
    intended -- "extraction may allocate this much MORE" -- and is the only
    form that survives the parent growing over time.

    Returns None when the parent's size cannot be read. No limit is then set,
    and isolation still contains a runaway: the child is the largest process,
    so the OOM killer takes it and the web worker lives. A weaker guarantee
    than the rlimit, but strictly better than refusing to extract at all.
    """
    parent = _parent_virtual_bytes()
    if parent is None:
        return None
    return parent + budget_bytes


def _child(conn, fn: Callable[..., Any], args: tuple, mem_bytes: int) -> None:
    """Child entry point: cap the address space, extract, ship the result.

    Exits with ``os._exit`` so the forked copy never runs the parent's atexit
    handlers or flushes its buffers -- this process is a calculator, not a
    server.
    """
    try:
        import resource

        limit = child_address_space_limit(mem_bytes)
        if limit is not None:
            try:
                soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                if hard != resource.RLIM_INFINITY:
                    limit = min(limit, hard)
                resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
            except (ValueError, OSError) as exc:
                # Refusing to set the limit must not mean refusing to extract,
                # and must NOT send a second message -- the parent reads one
                # and would take it as the result. Log and carry on: the child
                # is still the largest process, so the OOM killer takes it
                # rather than the web worker.
                logger.warning(
                    "could not set RLIMIT_AS in extraction child (%s); "
                    "continuing without it", exc,
                )
        os.environ[_IN_CHILD_ENV] = "1"
        result = fn(*args)
        _safe_send(conn, ("ok", result))
    except MemoryError:
        # The ceiling did its job. Distinguishable from a crash so the caller
        # can report the real reason rather than a generic failure.
        _safe_send(conn, ("memory", None))
    except BaseException as exc:  # noqa: BLE001 - the child must not hang
        _safe_send(conn, ("error", f"{type(exc).__name__}: {exc}"))
    finally:
        _safe_close(conn)
        os._exit(0)


def run_isolated(
    fn: Callable[..., Any],
    args: tuple,
    *,
    fallback: Any,
    label: str = "extraction",
) -> tuple[Any, dict[str, Any]]:
    """Run ``fn(*args)`` in a memory-capped child.

    Returns ``(result, diag)``. ``diag`` is empty on success and otherwise
    carries the reason, which the caller folds into the document's metadata so
    a 0-chunk document says WHY it is empty instead of looking like an empty
    file -- the ambiguity that hid the .doc extraction failure for months.

    Falls back to running in-process when isolation is unavailable, so
    behaviour on a dev box is unchanged.
    """
    if not isolation_available():
        return fn(*args), {}

    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child,
        args=(child_conn, fn, args, _MEM_MB * 1024 * 1024),
        daemon=True,
    )
    proc.start()
    child_conn.close()  # only the child writes; else the parent never sees EOF

    status, payload = "crash", None
    try:
        if parent_conn.poll(_TIMEOUT_S):
            status, payload = parent_conn.recv()
        else:
            status = "timeout"
    except EOFError:
        # Child died without sending -- the kernel OOM killer reaching the
        # child instead of RLIMIT_AS raising inside it.
        status = "crash"
    finally:
        parent_conn.close()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

    if status == "ok":
        return payload, {}

    reason = {
        "memory": f"{label} exceeded the {_MEM_MB} MB child limit",
        "timeout": f"{label} exceeded {_TIMEOUT_S:.0f}s",
        "crash": f"{label} child died (exitcode={proc.exitcode})",
    }.get(status, f"{label} failed: {payload}")

    logger.warning(
        "isolated %s failed: %s — returning empty result, service unaffected",
        label, reason,
    )
    return fallback, {"extract_failed": status, "extract_failed_detail": reason}
