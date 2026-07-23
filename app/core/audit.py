"""Append-only audit log — Roadmap V2 · Epic 6 (data governance).

Records who/what touched client documents — uploads, deletions, purges — so
the platform can answer "what happened to this data". Stored as JSONL in
DATA_DIR/audit.log.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_lock = threading.Lock()


def _audit_file() -> str:
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        import tempfile
        data_dir = tempfile.gettempdir()
    return os.path.join(data_dir, "audit.log")


def record(event: str, **details: Any) -> Dict[str, Any]:
    """Append an audit entry. Best-effort — never raises into the caller."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }
    try:
        with _lock:
            with open(_audit_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
    return entry


def read_audit(
    limit: int = 200, project_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return the most recent audit entries, optionally scoped to a project."""
    path = _audit_file()
    if not os.path.exists(path):
        return []
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if project_id and e.get("project_id") != project_id:
                continue
            entries.append(e)
    return entries[-limit:]
