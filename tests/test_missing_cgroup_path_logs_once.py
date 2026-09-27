"""A missing cgroup memory path is one log line and no traceback.

Fargate has no cgroup-v1 memory files. Reading them must not dump a
stack into the ingest log, and the snapshot must still return.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core import ingest_lifecycle as lifecycle


def _proc(tmp: Path) -> Path:
    proc = tmp / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "status").write_text(
        "Name:\tpython3\nVmRSS:\t 2048 kB\nVmHWM:\t 4096 kB\n",
        encoding="utf-8",
    )
    (proc / "uptime").write_text("100.0 1.0\n", encoding="utf-8")
    (proc / "self" / "cgroup").write_text("0::/\n", encoding="utf-8")
    return proc


def _warnings(caplog):
    return [
        record for record in caplog.records
        if record.name == "app.core.ingest_lifecycle"
        and record.levelno >= logging.INFO
    ]


def test_a_missing_cgroup_path_logs_once_without_a_traceback(tmp_path, caplog):
    """Several missing-path shapes. v2 and v1 files still read when present."""
    problems: list[str] = []

    def _check(label: str, root: Path, *, expect_warning: bool, expect_current):
        caplog.clear()
        try:
            with caplog.at_level(logging.INFO, logger="app.core.ingest_lifecycle"):
                snap = lifecycle.read_memory_snapshot(
                    proc_root=_proc(root), cgroup_root=root / "cgroup",
                )
        except Exception as exc:  # noqa: BLE001 — the assertion is "no raise"
            problems.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        notes = _warnings(caplog)
        text = caplog.text
        if "Traceback" in text:
            problems.append(f"{label}: traceback in log ({len(notes)} records)")
        if expect_warning:
            if len(notes) != 1:
                problems.append(
                    f"{label}: expected 1 log record, got {len(notes)}: "
                    + " | ".join(r.getMessage() for r in notes)
                )
            elif notes[0].exc_info is not None:
                problems.append(f"{label}: log record carries exc_info")
            elif "isn't available" not in notes[0].getMessage():
                problems.append(
                    f"{label}: message does not say the path isn't available: "
                    f"{notes[0].getMessage()!r}"
                )
        else:
            unavailable = [r for r in notes if "isn't available" in r.getMessage()]
            if unavailable:
                problems.append(f"{label}: warned that a present path isn't available")
            if any(r.exc_info for r in notes):
                problems.append(f"{label}: traceback on a present path")
        if snap.cgroup_current_mb != expect_current and not (
            expect_current is not None
            and snap.cgroup_current_mb is not None
            and abs(snap.cgroup_current_mb - expect_current) < 0.2
        ):
            problems.append(
                f"{label}: cgroup_current_mb={snap.cgroup_current_mb!r} "
                f"expected {expect_current!r}"
            )
        if snap.rss_mb is None:
            problems.append(f"{label}: rss was not read")

    empty = tmp_path / "empty"
    (empty / "cgroup").mkdir(parents=True)
    _check("empty-cgroup-dir", empty, expect_warning=True, expect_current=None)

    missing = tmp_path / "missing"
    missing.mkdir()
    _check("cgroup-root-absent", missing, expect_warning=True, expect_current=None)

    v2 = tmp_path / "v2"
    cg = v2 / "cgroup"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("1048576", encoding="utf-8")
    (cg / "memory.max").write_text("2097152", encoding="utf-8")
    _check("cgroup-v2-files", v2, expect_warning=False, expect_current=1.0)

    v1 = tmp_path / "v1"
    mem = v1 / "cgroup" / "memory"
    mem.mkdir(parents=True)
    (mem / "memory.usage_in_bytes").write_text("2097152", encoding="utf-8")
    (mem / "memory.limit_in_bytes").write_text("4194304", encoding="utf-8")
    _check("cgroup-v1-files", v1, expect_warning=False, expect_current=2.0)

    unlimited = tmp_path / "unlimited"
    ucg = unlimited / "cgroup"
    ucg.mkdir(parents=True)
    (ucg / "memory.current").write_text("1048576", encoding="utf-8")
    (ucg / "memory.max").write_text("max", encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.core.ingest_lifecycle"):
        snap = lifecycle.read_memory_snapshot(
            proc_root=_proc(unlimited), cgroup_root=ucg,
        )
    if snap.cgroup_limit_mb is not None:
        problems.append(f"limit-max: limit parsed as {snap.cgroup_limit_mb}")
    if "Traceback" in caplog.text:
        problems.append("limit-max: traceback")

    assert not problems, "\n".join(problems)
