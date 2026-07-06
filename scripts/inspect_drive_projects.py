#!/usr/bin/env python
"""Inspect approved/Drive-linked projects and emit a re-import manifest.

Usage:
  export FORK_API_KEY=...
  python scripts/inspect_drive_projects.py

Output columns:
  project_id | name | origin | docs | files_present | zero_chunk_docs | total_chunks | folder_id

folder_id is read from the optional GDRIVE_PROJECT_FOLDERS env var; if unset,
it is left blank for manual fill-in.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE = os.getenv("FORK_BASE_URL", "https://the-fork.onrender.com")


def _auth_header(base: str) -> dict:
    tok = os.getenv("FORK_TOKEN") or os.getenv("FORK_API_KEY")
    if not tok:
        email, pw = os.getenv("FORK_EMAIL"), os.getenv("FORK_PASSWORD")
        if not (email and pw):
            sys.exit("No credentials: set FORK_TOKEN / FORK_API_KEY / FORK_EMAIL+FORK_PASSWORD")
        r = httpx.post(f"{base}/v1/users/login", json={"email": email, "password": pw}, timeout=30)
        r.raise_for_status()
        tok = r.json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _folder_map() -> Dict[str, str]:
    raw = (os.getenv("GDRIVE_PROJECT_FOLDERS") or "").strip()
    out: Dict[str, str] = {}
    if not raw:
        return out
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        pid, fid = chunk.split(":", 1)
        out[pid.strip()] = fid.strip()
    return out


def _inspect_project(client: httpx.Client, proj: Dict[str, Any]) -> Dict[str, Any]:
    pid = proj["id"]
    docs: List[Dict[str, Any]] = []
    try:
        # Project detail enriches chunk_count when doc_count is small enough.
        r = client.get(f"/v1/projects/{pid}")
        r.raise_for_status()
        detail = r.json()
        docs = detail.get("documents", []) or []
        total = detail.get("document_count", len(docs))
    except httpx.HTTPError as exc:
        print(f"[warn] could not inspect {pid}: {exc}", file=sys.stderr)
        total = proj.get("document_count", 0)

    files_present = sum(1 for d in docs if d.get("file_path") and d.get("size", 0) > 0)
    # chunk_count is present only when detail enrichment ran.
    chunk_counts = [d.get("chunk_count") for d in docs if "chunk_count" in d]
    zero_chunk = sum(1 for c in chunk_counts if (c or 0) == 0)
    total_chunks = sum(c or 0 for c in chunk_counts)
    return {
        "project_id": pid,
        "name": proj.get("name", ""),
        "origin": proj.get("origin", ""),
        "docs": total,
        "files_present": files_present,
        "zero_chunk_docs": zero_chunk if chunk_counts else None,
        "total_chunks": total_chunks if chunk_counts else None,
    }


def main() -> int:
    base = os.getenv("FORK_BASE_URL", DEFAULT_BASE)
    headers = _auth_header(base)
    folder_map = _folder_map()

    with httpx.Client(base_url=base, headers=headers, timeout=60) as client:
        r = client.get("/v1/projects")
        r.raise_for_status()
        projects = r.json().get("projects", []) or []

        # Focus on Drive-linked / admin-approved / master corpus projects.
        candidates = [
            p for p in projects
            if p.get("origin") in ("admin_drive_approved",)
            or p.get("is_master_corpus")
            or p.get("id") in ("projects_folder", "training_material")
        ]

        print("| project_id | name | origin | docs | files_present | zero_chunk_docs | total_chunks | folder_id |")
        print("|---|---|---|---|---|---|---|---|")
        for p in candidates:
            info = _inspect_project(client, p)
            folder_id = folder_map.get(info["project_id"], "")
            print(
                f"| {info['project_id']} | {info['name']} | {info['origin']} | "
                f"{info['docs']} | {info['files_present']} | "
                f"{info['zero_chunk_docs'] if info['zero_chunk_docs'] is not None else 'N/A'} | "
                f"{info['total_chunks'] if info['total_chunks'] is not None else 'N/A'} | {folder_id} |"
            )

    print("\nMissing folder_id entries need to be provided in GDRIVE_PROJECT_FOLDERS or manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
