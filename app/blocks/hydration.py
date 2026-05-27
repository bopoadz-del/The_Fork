"""Hydration Block — nightly "sleep on it" pass.

Runs once a day (intended schedule: 01:00 UTC, see ``app/core/hydration_scheduler.py``)
and does two things for the previous calendar day:

1. **Lessons learned from users**. Reads every conversation that had activity
   in the window, groups by project, asks the ChatBlock to distill the user's
   actual asks, friction points, and recurring patterns into a short markdown
   brief. One brief per project that had activity, plus one global rollup
   across the whole tenant.

2. **Get familiar with the files**. Walks each configured drive connector
   (``local_drive`` always; ``google_drive``/``onedrive`` when their tokens
   are present), and for every file under a project asks the document
   indexer to incrementally index it. The indexer's existing fingerprint
   check makes this cheap for unchanged files.

Output rows go to ``app/core/hydration_store.py`` (SQLite at
``$DATA_DIR/hydration.db``) and are served via ``GET /v1/hydration/latest``.
Nothing is auto-surfaced to end users in chat — these are operator/admin
lessons learned, not user-facing morning briefs.

Failures inside one project must not abort the whole run — each project is
isolated, errors are captured per-project in the row's ``facts`` payload.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.universal_base import UniversalBlock


logger = logging.getLogger(__name__)


class HydrationBlock(UniversalBlock):
    name = "hydration"
    version = "1.0.0"
    description = (
        "Nightly hydration: distill user-driven lessons learned from the day's "
        "conversations and freshen the document index from connected drives."
    )
    layer = 4  # runs over other blocks (chat, drives, doc_index)
    tags = ["ops", "scheduled", "summarization", "indexing", "memory"]
    requires = []  # talks to chat/drive blocks via BLOCK_REGISTRY at call time

    default_config = {
        "max_messages_per_project": 400,  # cap LLM input size
        "summary_max_tokens": 600,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": (
                '{"operation": "run"}  '
                '// or {"operation": "latest", "scope": "global"}  '
                '// or {"operation": "latest", "scope": "project", "project_id": "p1"}'
            ),
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "status", "type": "text", "label": "Status"},
                {"name": "run_date", "type": "text", "label": "Date"},
                {"name": "projects_processed", "type": "number", "label": "Projects"},
                {"name": "files_indexed", "type": "number", "label": "Files indexed"},
                {"name": "summary_md", "type": "markdown", "label": "Summary"},
            ],
        },
        "quick_actions": [
            {"icon": "🌙", "label": "Run now", "prompt": '{"operation":"run"}'},
            {"icon": "📅", "label": "Latest global",
             "prompt": '{"operation":"latest","scope":"global"}'},
        ],
    }

    # ── operation dispatch ────────────────────────────────────────────────

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        # Accept the operation from either input_data (JSON body) or params.
        op = None
        if isinstance(input_data, dict):
            op = input_data.get("operation")
            params = {**input_data, **params}
        op = op or params.get("operation") or "run"

        if op == "run":
            return await self._op_run(params)
        if op == "latest":
            return self._op_latest(params)
        if op == "history":
            return self._op_history(params)
        return {"status": "error", "error": f"unknown operation: {op!r}"}

    # ── operations ────────────────────────────────────────────────────────

    async def _op_run(self, params: Dict) -> Dict:
        """Execute one hydration pass.

        ``target_date`` (YYYY-MM-DD) overrides the default of "yesterday UTC".
        ``project_ids`` (list) overrides auto-detection from the conversation
        store — useful for forced re-runs of a single project.
        """
        from app.core import hydration_store

        target_date = params.get("target_date") or _yesterday_utc_iso()
        window_start, window_end = _utc_day_bounds(target_date)

        # 1. Detect which projects had activity in the window
        forced = params.get("project_ids")
        if forced:
            project_ids: List[str] = [str(p) for p in forced if p]
        else:
            project_ids = _projects_active_in_window(window_start, window_end)

        results_per_project: List[Dict[str, Any]] = []
        total_files_indexed = 0
        global_errors: List[str] = []

        for pid in project_ids:
            try:
                row = await self._hydrate_project(
                    pid, target_date, window_start, window_end, params
                )
                results_per_project.append(row)
                total_files_indexed += row.get("files_indexed", 0)
            except Exception as exc:  # noqa: BLE001 — never abort the whole pass
                logger.exception("hydration: project %s failed", pid)
                global_errors.append(f"{pid}: {type(exc).__name__}: {exc}")

        # 2. Global rollup across all projects
        global_facts = {
            "projects_processed": len(results_per_project),
            "total_files_indexed": total_files_indexed,
            "project_ids": [r["project_id"] for r in results_per_project],
            "per_project_message_counts": {
                r["project_id"]: r.get("messages_seen", 0)
                for r in results_per_project
            },
            "errors": global_errors,
        }
        global_summary_md, global_provider = await self._summarize_global(
            results_per_project, target_date, params
        )
        if global_provider in ("offline_template", "unavailable", "error"):
            global_summary_md = _heuristic_global_summary(
                target_date, results_per_project, total_files_indexed, global_errors
            )
        hydration_store.record_run(
            run_date=target_date,
            scope="global",
            project_id=None,
            summary_md=global_summary_md,
            facts=global_facts,
            provider=global_provider,
        )

        return {
            "status": "success",
            "run_date": target_date,
            "projects_processed": len(results_per_project),
            "files_indexed": total_files_indexed,
            "errors": global_errors,
            "summary_md": global_summary_md,
        }

    def _op_latest(self, params: Dict) -> Dict:
        from app.core import hydration_store

        scope = params.get("scope") or "global"
        if scope not in ("global", "project"):
            return {"status": "error", "error": f"invalid scope: {scope!r}"}
        project_id = params.get("project_id") if scope == "project" else None
        if scope == "project" and not project_id:
            return {"status": "error", "error": "project scope requires project_id"}
        row = hydration_store.get_latest(scope, project_id)
        if not row:
            return {"status": "empty", "scope": scope, "project_id": project_id}
        return {"status": "success", **row}

    def _op_history(self, params: Dict) -> Dict:
        from app.core import hydration_store

        rows = hydration_store.list_history(
            scope=params.get("scope"),
            project_id=params.get("project_id"),
            limit=int(params.get("limit") or 20),
        )
        return {"status": "success", "count": len(rows), "runs": rows}

    # ── per-project work ──────────────────────────────────────────────────

    async def _hydrate_project(
        self,
        project_id: str,
        run_date: str,
        window_start: str,
        window_end: str,
        params: Dict,
    ) -> Dict[str, Any]:
        from app.core import hydration_store

        messages = _collect_project_messages(project_id, window_start, window_end)
        cap = int(self.config.get("max_messages_per_project") or 400)
        if len(messages) > cap:
            messages = messages[-cap:]  # keep most recent within cap

        # Step 1: discover net-new files that appeared on the local drive's
        # per-project drop folder since last hydration, and attach them so the
        # subsequent reindex loop will pick them up.
        new_files_attached, attach_errors = _discover_local_drive_files(project_id)

        # Step 1b: same idea for Google Drive — when GDRIVE_PROJECT_FOLDERS
        # has a mapping for this project, list the configured folder via the
        # service account and attach anything we haven't seen before.
        gdrive_attached, gdrive_errors = _discover_gdrive_files(project_id)
        new_files_attached += gdrive_attached
        attach_errors.extend(gdrive_errors)

        # Step 2: refresh every known document's index (cheap when unchanged
        # thanks to the doc indexer's fingerprint check).
        files_indexed, files_skipped, file_errors = await _reindex_project_drives(project_id)

        summary_md, provider = await self._summarize_project(
            project_id, messages, run_date, files_indexed, params
        )
        # When the LLM is unreachable, enrich the offline summary with a
        # heuristic so the row is informative instead of a placeholder.
        if provider in ("offline_template", "unavailable", "error"):
            summary_md = _heuristic_project_summary(
                project_id, run_date, messages, files_indexed, new_files_attached
            )

        facts = {
            "messages_seen": len(messages),
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "file_errors": file_errors,
            "new_files_attached": new_files_attached,
            "attach_errors": attach_errors,
        }
        hydration_store.record_run(
            run_date=run_date,
            scope="project",
            project_id=project_id,
            summary_md=summary_md,
            facts=facts,
            provider=provider,
        )
        return {
            "project_id": project_id,
            "messages_seen": len(messages),
            "files_indexed": files_indexed,
            "new_files_attached": new_files_attached,
            "summary_md": summary_md,
        }

    # ── summarization (delegates to ChatBlock) ────────────────────────────

    async def _summarize_project(
        self,
        project_id: str,
        messages: List[Dict[str, Any]],
        run_date: str,
        files_indexed: int,
        params: Dict,
    ) -> tuple[str, str]:
        prompt = _build_project_summary_prompt(project_id, run_date, messages, files_indexed)
        return await _call_chat(prompt, max_tokens=int(self.config.get("summary_max_tokens") or 600))

    async def _summarize_global(
        self,
        per_project: List[Dict[str, Any]],
        run_date: str,
        params: Dict,
    ) -> tuple[str, str]:
        prompt = _build_global_summary_prompt(run_date, per_project)
        return await _call_chat(prompt, max_tokens=int(self.config.get("summary_max_tokens") or 600))


# ── helpers (module-level so tests can monkey-patch them) ─────────────────

def _yesterday_utc_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _utc_day_bounds(date_iso: str) -> tuple[str, str]:
    """Return (start, end) ISO-8601 timestamps for the UTC calendar day."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = d.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (d + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end


def _projects_active_in_window(window_start: str, window_end: str) -> List[str]:
    """Distinct project_ids whose conversations were updated in the window."""
    from app.core import agent_memory

    convs = agent_memory.list_conversations()
    seen: List[str] = []
    for c in convs:
        updated = c.get("updated_at") or c.get("created_at") or ""
        if window_start <= updated < window_end:
            pid = c.get("project_id")
            if pid and pid not in seen:
                seen.append(pid)
    return seen


def _collect_project_messages(
    project_id: str, window_start: str, window_end: str
) -> List[Dict[str, Any]]:
    """All messages across the project's conversations whose timestamp falls
    inside the window. Returned in oldest-first order across conversations."""
    from app.core import agent_memory

    convs = agent_memory.list_conversations(project_id=project_id)
    out: List[Dict[str, Any]] = []
    for c in convs:
        msgs = agent_memory.get_messages(c["id"], limit=500)
        for m in msgs:
            ts = m.get("created_at") or ""
            if window_start <= ts < window_end:
                out.append({
                    "conversation_id": c["id"],
                    "role": m.get("role"),
                    "content": m.get("content") or "",
                    "created_at": ts,
                })
    out.sort(key=lambda m: m.get("created_at") or "")
    return out


async def _reindex_project_drives(project_id: str) -> tuple[int, int, List[str]]:
    """Walk available drive connectors and reindex any documents discovered
    for the project. Returns (indexed_count, skipped_count, error_messages).

    The doc indexer is keyed by ``project_id`` + ``document_id`` — drives
    don't natively expose a project_id concept, so we treat each project_id
    that already has indexed documents as a known project, and refresh those
    documents' content from disk. New uploads land via the upload router and
    are auto-indexed there; this pass is for "files changed on disk since the
    upload" — i.e., a corrected drawing replacing the previous version.
    """
    from app.core import doc_index

    indexed = 0
    skipped = 0
    errors: List[str] = []

    existing = doc_index._load_index(project_id) or {}
    for doc in existing.get("documents", []) or []:
        doc_id = doc.get("document_id")
        if not doc_id:
            continue
        try:
            result = doc_index.index_document(project_id, doc_id)
            if result.get("status") == "indexed":
                indexed += 1
            elif result.get("status") == "unchanged":
                skipped += 1
            else:
                # treat any other terminal status as a skip rather than error
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{doc_id}: {type(exc).__name__}: {exc}")
    return indexed, skipped, errors


def _build_project_summary_prompt(
    project_id: str,
    run_date: str,
    messages: List[Dict[str, Any]],
    files_indexed: int,
) -> str:
    transcript = "\n".join(
        f"[{m['role']}] {m['content']}"[:600] for m in messages
    ) or "(no user activity)"
    return (
        "You are a hydration agent producing operator-facing 'lessons learned' "
        f"for project '{project_id}' on {run_date}.\n\n"
        "Read the transcript below and respond in markdown with EXACTLY these sections:\n"
        "## What users asked for\n"
        "## Where they hit friction\n"
        "## Recurring patterns or themes\n"
        "## Lessons learned for the platform\n\n"
        "Be terse. Bullet points. Quote short user phrases when illustrative. "
        "Do NOT invent activity that is not in the transcript. If the transcript "
        "is empty, say 'No user activity in window' under each section.\n\n"
        f"Files re-indexed today: {files_indexed}\n\n"
        "TRANSCRIPT:\n"
        f"{transcript}\n"
    )


def _build_global_summary_prompt(
    run_date: str, per_project: List[Dict[str, Any]]
) -> str:
    rollup = "\n\n".join(
        f"### Project {p['project_id']}\n"
        f"- Messages: {p.get('messages_seen', 0)}\n"
        f"- Files indexed: {p.get('files_indexed', 0)}\n"
        f"{p.get('summary_md', '')[:1500]}"
        for p in per_project
    ) or "(no projects had activity)"
    return (
        f"You are producing the global hydration rollup for {run_date}.\n\n"
        "Below are per-project summaries already written. Produce a single "
        "tenant-wide markdown brief with EXACTLY these sections:\n"
        "## Activity at a glance\n"
        "## Cross-project lessons learned\n"
        "## Platform-level action items\n\n"
        "Be terse. Use bullet points. Do NOT repeat per-project detail verbatim — "
        "look for the cross-cutting signal.\n\n"
        "PER-PROJECT SUMMARIES:\n"
        f"{rollup}\n"
    )


def _local_drive_root() -> str:
    """Same convention as ``app/blocks/local_drive.py``: confined drive root."""
    root = os.path.realpath(
        os.getenv("LOCAL_DRIVE_ROOT") or os.getenv("DATA_DIR", "./data")
    )
    return root


def _project_dropbox(project_id: str) -> str:
    """Per-project drop folder. Files placed here outside of the upload flow
    get auto-attached overnight. Kept under the same root LocalDriveBlock
    sandboxes to, so path-escape rules already apply."""
    return os.path.join(_local_drive_root(), "projects", project_id, "dropbox")


# Same allowlist the upload router enforces — keep them in lockstep so a file
# the hydration job auto-attaches would also have been accepted by the UI.
_ATTACH_ALLOWED_EXTS = {
    ".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
    ".dwg", ".dxf", ".ifc", ".xer", ".mpp",
}


def _discover_local_drive_files(project_id: str) -> Tuple[int, List[str]]:
    """Walk the project's drop folder and attach any file that isn't already
    a registered document. Returns (attached_count, errors).

    The walk is bounded by the LocalDriveBlock's sandbox root — anything that
    would escape it (a symlink pointing out, etc.) is silently skipped. Files
    with disallowed extensions are skipped. Hidden files (dotfiles) are
    skipped. Files larger than ``HYDRATION_MAX_ATTACH_SIZE`` (default 50 MB)
    are skipped to avoid pulling in giant artifacts unintentionally.
    """
    from app.core import projects as projects_store

    dropbox = _project_dropbox(project_id)
    if not os.path.isdir(dropbox):
        return 0, []

    drive_root = _local_drive_root()
    try:
        max_bytes = int(os.getenv("HYDRATION_MAX_ATTACH_SIZE", "52428800"))
    except ValueError:
        max_bytes = 52428800  # 50 MB

    # Build a set of paths already attached to this project so we don't
    # re-register a file. Compare by realpath to handle symlink shenanigans.
    existing = projects_store.list_documents(project_id)
    existing_paths = {
        os.path.realpath(d.get("file_path"))
        for d in existing
        if d.get("file_path")
    }

    attached = 0
    errors: List[str] = []
    for dirpath, dirnames, filenames in os.walk(dropbox, followlinks=False):
        # Skip hidden subdirs in-place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                real = os.path.realpath(full)
                # Sandbox check: the realpath must stay under the drive root.
                if not (real == drive_root or real.startswith(drive_root + os.sep)):
                    continue
                _, ext = os.path.splitext(name.lower())
                if ext not in _ATTACH_ALLOWED_EXTS:
                    continue
                if real in existing_paths:
                    continue
                size = os.path.getsize(real)
                if size > max_bytes:
                    errors.append(f"{name}: oversize ({size} > {max_bytes})")
                    continue
                doc = projects_store.add_document(
                    project_id=project_id,
                    original_name=name,
                    stored_as=os.path.relpath(real, drive_root),
                    file_path=real,
                    size=size,
                )
                attached += 1
                existing_paths.add(real)
                logger.info(
                    "hydration: attached %s to project %s as %s",
                    name, project_id, doc.get("id"),
                )
            except Exception as exc:  # noqa: BLE001 — never abort the walk
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return attached, errors


# ── Google Drive discovery (service-account path) ─────────────────────────


def _gdrive_seen_path() -> str:
    """Sidecar JSON tracking which Drive file IDs we've already attached
    per project. Lives next to hydration.db. A sidecar is enough — adding
    a column to the documents table would force a migration just to record
    one external identifier."""
    return os.path.join(os.getenv("DATA_DIR", "./data"), "hydration_gdrive_seen.json")


def _load_gdrive_seen() -> Dict[str, List[str]]:
    path = _gdrive_seen_path()
    if not os.path.isfile(path):
        return {}
    try:
        import json as _json
        with open(path, "r") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            return {}
        # Coerce values to list[str] defensively
        out: Dict[str, List[str]] = {}
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v]
        return out
    except Exception:  # noqa: BLE001 — corrupt sidecar shouldn't kill hydration
        return {}


def _save_gdrive_seen(seen: Dict[str, List[str]]) -> None:
    import json as _json
    path = _gdrive_seen_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        _json.dump(seen, f, ensure_ascii=False)
    os.replace(tmp, path)


def _discover_gdrive_files(project_id: str) -> Tuple[int, List[str]]:
    """Walk the configured Drive folder for ``project_id`` and attach any
    file we haven't seen before. Returns (attached_count, errors).

    Behavior matches the local-drive discovery as closely as possible:
    same extension allowlist, same per-file size cap, same idempotency
    guarantee (a re-run attaches zero). Google-native Docs/Sheets are
    skipped — those need an ``export`` call rather than a binary download,
    which is a separate piece of plumbing.

    The path stays cleanly disabled when nothing is configured: no key →
    no mapping → no calls → no errors. The function returns ``(0, [])``
    in that case so hydration doesn't even log noise.
    """
    from app.core import gdrive_service, projects as projects_store

    mapping = gdrive_service.parse_project_folder_map()
    folder_id = mapping.get(project_id)
    if not folder_id:
        return 0, []  # not configured for this project — fine

    if not gdrive_service.is_configured():
        return 0, [
            "gdrive: GDRIVE_PROJECT_FOLDERS set but GDRIVE_SERVICE_ACCOUNT_JSON is missing"
        ]

    files, list_err = gdrive_service.list_folder_files(folder_id)
    if list_err:
        return 0, [f"gdrive list({folder_id}): {list_err}"]

    seen_all = _load_gdrive_seen()
    seen_for_project = set(seen_all.get(project_id, []))

    try:
        max_bytes = int(os.getenv("HYDRATION_MAX_ATTACH_SIZE", "52428800"))
    except ValueError:
        max_bytes = 52428800

    attached = 0
    errors: List[str] = []
    data_dir = os.getenv("DATA_DIR", "./data")
    os.makedirs(data_dir, exist_ok=True)

    # file_crypto is the same module the upload router uses — write through
    # it so encryption-at-rest (when DATA_ENCRYPTION_KEY is set) applies to
    # Drive-pulled files identically to uploaded ones.
    try:
        from app.core import file_crypto
    except ImportError:
        file_crypto = None  # type: ignore[assignment]

    for f_meta in files:
        fid = f_meta.get("id")
        name = (f_meta.get("name") or "").strip()
        if not fid or not name:
            continue
        if fid in seen_for_project:
            continue
        if not gdrive_service.is_downloadable(f_meta):
            # Google-native doc — skip (would need export endpoint)
            seen_for_project.add(fid)
            continue
        _, ext = os.path.splitext(name.lower())
        if ext not in _ATTACH_ALLOWED_EXTS:
            errors.append(f"gdrive {name}: disallowed extension {ext}")
            seen_for_project.add(fid)
            continue
        # Drive returns size as a string in the JSON metadata
        try:
            advertised_size = int(f_meta.get("size") or "0")
        except (TypeError, ValueError):
            advertised_size = 0
        if advertised_size and advertised_size > max_bytes:
            errors.append(f"gdrive {name}: oversize ({advertised_size} > {max_bytes})")
            seen_for_project.add(fid)
            continue

        blob, dl_err = gdrive_service.download_file_bytes(fid)
        if blob is None:
            errors.append(f"gdrive {name}: {dl_err}")
            continue
        if len(blob) > max_bytes:
            errors.append(f"gdrive {name}: post-download oversize ({len(blob)} > {max_bytes})")
            seen_for_project.add(fid)
            continue

        try:
            import uuid as _uuid
            stored_as = f"{_uuid.uuid4().hex[:8]}_{name}"
            filepath = os.path.join(data_dir, stored_as)
            if file_crypto is not None:
                file_crypto.write_document(filepath, blob)
            else:
                with open(filepath, "wb") as out:
                    out.write(blob)
            doc = projects_store.add_document(
                project_id=project_id,
                original_name=name,
                stored_as=stored_as,
                file_path=filepath,
                size=len(blob),
            )
            attached += 1
            seen_for_project.add(fid)
            logger.info(
                "hydration: attached Drive file %s (%s) to project %s as %s",
                name, fid, project_id, doc.get("id"),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gdrive {name}: attach failed: {type(exc).__name__}: {exc}")

    seen_all[project_id] = sorted(seen_for_project)
    try:
        _save_gdrive_seen(seen_all)
    except Exception as exc:  # noqa: BLE001 — sidecar write failure is recoverable
        errors.append(f"gdrive: sidecar save failed: {exc}")

    return attached, errors


# ── Heuristic summaries (used when ChatBlock has no model to call) ─────────

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "for", "in", "on",
    "at", "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "this", "that", "these",
    "those", "it", "its", "i", "you", "we", "they", "he", "she", "them", "us",
    "my", "your", "our", "their", "his", "her", "what", "when", "where", "why",
    "how", "which", "who", "whom", "can", "could", "should", "would", "will",
    "shall", "may", "might", "must", "not", "no", "yes", "so", "than", "then",
    "also", "just", "very", "any", "some", "all", "each", "more", "most", "less",
    "least", "out", "up", "down", "into", "about", "over", "under",
}

_WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _top_keywords(messages: List[Dict[str, Any]], n: int = 8) -> List[Tuple[str, int]]:
    counter: Counter[str] = Counter()
    for m in messages:
        if m.get("role") != "user":
            continue
        for tok in _WORD_RX.findall(m.get("content") or ""):
            t = tok.lower()
            if t in _STOPWORDS:
                continue
            counter[t] += 1
    return counter.most_common(n)


def _user_friction_signals(messages: List[Dict[str, Any]]) -> List[str]:
    """Cheap heuristics that flag likely friction: repeated questions, short
    user messages followed by long retries, explicit complaint markers."""
    signals: List[str] = []
    complaint_rx = re.compile(
        r"\b(error|broken|doesn'?t work|not working|wrong|fail(ed|s)?|"
        r"missing|empty|why|stuck|help)\b",
        re.IGNORECASE,
    )
    user_msgs = [m for m in messages if m.get("role") == "user"]
    complaints = sum(1 for m in user_msgs if complaint_rx.search(m.get("content") or ""))
    if complaints:
        signals.append(f"{complaints} user message(s) used complaint/error language")

    # Repeated near-duplicate questions (same first 40 chars) — a sign the
    # user asked twice because the first answer didn't land.
    prefixes = [
        (m.get("content") or "")[:40].strip().lower()
        for m in user_msgs
        if (m.get("content") or "").strip()
    ]
    dup_count = sum(c - 1 for c in Counter(prefixes).values() if c > 1)
    if dup_count:
        signals.append(f"{dup_count} user message(s) appear to repeat earlier asks")
    return signals


def _heuristic_project_summary(
    project_id: str,
    run_date: str,
    messages: List[Dict[str, Any]],
    files_indexed: int,
    new_files_attached: int,
) -> str:
    """Structured non-LLM summary. Reads real signals from the day's data so
    the row is useful even when no model was reachable."""
    user_msgs = [m for m in messages if m.get("role") == "user"]
    asst_msgs = [m for m in messages if m.get("role") == "assistant"]

    asks_section = "_No user activity in window._"
    if user_msgs:
        # First 5 unique user asks, trimmed
        seen = set()
        lines = []
        for m in user_msgs:
            t = (m.get("content") or "").strip().replace("\n", " ")
            key = t[:60].lower()
            if not t or key in seen:
                continue
            seen.add(key)
            lines.append(f"- {t[:160]}{'…' if len(t) > 160 else ''}")
            if len(lines) >= 5:
                break
        asks_section = "\n".join(lines)

    keywords = _top_keywords(messages)
    themes_section = (
        "\n".join(f"- `{w}` × {c}" for w, c in keywords)
        if keywords else "_No content to analyze._"
    )

    friction = _user_friction_signals(messages)
    friction_section = (
        "\n".join(f"- {s}" for s in friction)
        if friction else "_No obvious friction signals._"
    )

    lessons: List[str] = []
    if not messages:
        lessons.append("Project was idle today — nothing to learn.")
    if new_files_attached:
        lessons.append(
            f"{new_files_attached} new file(s) appeared on the drive without going "
            f"through the upload flow — operators are using the drop folder."
        )
    if files_indexed and files_indexed > 0:
        lessons.append(f"{files_indexed} document(s) re-indexed; index is current.")
    if friction:
        lessons.append("Friction signals detected — review the asks above for retry patterns.")
    if not lessons:
        lessons.append("No notable lessons for the platform today.")

    return (
        f"# {project_id} — {run_date} (heuristic; no LLM reached)\n\n"
        f"_Stats: {len(user_msgs)} user msg, {len(asst_msgs)} assistant msg, "
        f"{files_indexed} files re-indexed, {new_files_attached} new files attached._\n\n"
        f"## What users asked for\n{asks_section}\n\n"
        f"## Where they hit friction\n{friction_section}\n\n"
        f"## Recurring patterns or themes\n{themes_section}\n\n"
        f"## Lessons learned for the platform\n"
        + "\n".join(f"- {l}" for l in lessons)
        + "\n"
    )


def _heuristic_global_summary(
    run_date: str,
    per_project: List[Dict[str, Any]],
    total_files_indexed: int,
    errors: List[str],
) -> str:
    if not per_project:
        return (
            f"# Global hydration — {run_date} (heuristic; no LLM reached)\n\n"
            "## Activity at a glance\n- No projects had user activity today.\n\n"
            "## Cross-project lessons learned\n- N/A.\n\n"
            "## Platform-level action items\n- None.\n"
        )
    # Sort projects by message volume for "busiest"
    busiest = sorted(
        per_project, key=lambda p: p.get("messages_seen", 0), reverse=True
    )[:5]
    activity_lines = [
        f"- **{p['project_id']}**: {p.get('messages_seen', 0)} msg, "
        f"{p.get('files_indexed', 0)} files re-indexed, "
        f"{p.get('new_files_attached', 0)} new files attached"
        for p in busiest
    ]
    err_section = (
        "\n".join(f"- `{e}`" for e in errors[:10])
        if errors else "- No project-level failures.\n"
    )
    return (
        f"# Global hydration — {run_date} (heuristic; no LLM reached)\n\n"
        f"_Totals: {len(per_project)} projects active, "
        f"{total_files_indexed} files re-indexed, {len(errors)} project errors._\n\n"
        f"## Activity at a glance\n" + "\n".join(activity_lines) + "\n\n"
        f"## Cross-project lessons learned\n"
        f"- Per-project detail lives in the project-scoped rows; this rollup is "
        f"heuristic and surfaces volume + error signal only.\n\n"
        f"## Platform-level action items\n{err_section}\n"
    )


async def _call_chat(prompt: str, max_tokens: int = 600) -> tuple[str, str]:
    """Invoke the ChatBlock and return (text, provider). The ChatBlock's
    fallback chain (DeepSeek → Ollama → llama.cpp → offline template) guarantees
    a response so hydration never crashes on a missing model."""
    from app.blocks import BLOCK_REGISTRY

    cls = BLOCK_REGISTRY.get("chat")
    if cls is None:
        return ("_Chat block not available; no summary produced._", "unavailable")
    block = cls()
    try:
        resp = await block.execute(
            {"text": prompt},
            {"max_tokens": max_tokens, "temperature": 0.2},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hydration: chat call failed: %s", exc)
        return (f"_Summarizer failed: {type(exc).__name__}_", "error")

    text = ""
    if isinstance(resp, dict):
        text = (
            resp.get("response")
            or resp.get("text")
            or resp.get("message")
            or ""
        )
    provider = (resp.get("provider") if isinstance(resp, dict) else None) or "unknown"
    return (text or "_(empty response)_", provider)
