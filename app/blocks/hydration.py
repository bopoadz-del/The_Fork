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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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

        files_indexed, files_skipped, file_errors = await _reindex_project_drives(project_id)

        summary_md, provider = await self._summarize_project(
            project_id, messages, run_date, files_indexed, params
        )

        facts = {
            "messages_seen": len(messages),
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "file_errors": file_errors,
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
