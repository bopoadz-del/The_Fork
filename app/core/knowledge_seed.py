"""Idempotent boot-time seeding of bundled knowledge docs into the RAG
general-knowledge project (``training_material`` by default).

The markdown files in ``docs/knowledge/`` (CESMM/POMI units reference, FIDIC
contracts reference, procedures, etc.) are bundled into the image (see the
``!docs/knowledge/*.md`` allow-rule in .dockerignore) and ingested here on
startup, so they are retrievable across every project without a manual upload.

Runs from ``app.main`` lifespan, matching the ``_bootstrap_first_user`` pattern:
idempotent by content SHA (unchanged files are skipped; a changed file replaces
the old version by filename), and NEVER raises — a seeding failure must not
block boot.
"""
import glob
import hashlib
import logging
import os
import uuid

logger = logging.getLogger(__name__)

# repo root = app/core/knowledge_seed.py -> app/core -> app -> root
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KNOWLEDGE_DIR = os.path.join(_ROOT, "docs", "knowledge")


def _gk_project_id() -> str:
    raw = os.getenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "training_material")
    return next((p.strip() for p in raw.split(",") if p.strip()), "")


def seed_knowledge() -> None:
    """Ingest docs/knowledge/*.md into the GK project. Idempotent; never raises."""
    try:
        gk = _gk_project_id()
        if not gk:
            return
        files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md")))
        if not files:
            logger.info("knowledge seed: no *.md in %s", KNOWLEDGE_DIR)
            return

        from app.core import projects as store
        from app.core import doc_index, file_crypto

        if not store.get_project(gk):
            try:
                store.create_project(
                    "General Knowledge", user_id="system",
                    project_id=gk, origin="system_seed",
                )
                logger.info("knowledge seed: created GK project '%s'", gk)
            except Exception as e:  # noqa: BLE001 — a race/existing row is fine
                logger.warning("knowledge seed: create GK project '%s' failed: %s", gk, e)

        data_dir = os.getenv("DATA_DIR", "./data")
        os.makedirs(data_dir, exist_ok=True)
        seeded = 0
        for path in files:
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
                sha = hashlib.sha256(raw).hexdigest()
                name = os.path.basename(path)

                # Exact content already present -> nothing to do (idempotent).
                if store.find_document_by_sha(gk, sha):
                    continue

                # New or changed: drop any stale doc(s) with the same filename
                # so a re-edited knowledge file REPLACES rather than duplicates.
                for d in store.list_documents(gk):
                    if (d.get("original_name") or d.get("name")) == name:
                        try:
                            store.delete_document(d["id"])
                        except Exception:  # noqa: BLE001
                            pass

                file_id = str(uuid.uuid4())[:8]
                stored_as = f"{file_id}_{name}"
                filepath = os.path.join(data_dir, stored_as)
                file_crypto.write_document(filepath, raw)
                doc = store.add_document(
                    gk, name, stored_as, filepath, len(raw), content_sha256=sha,
                )
                doc_index.index_document(gk, doc["id"])
                seeded += 1
                logger.info("knowledge seed: ingested '%s'", name)
            except Exception as e:  # noqa: BLE001 — one bad file must not stop the rest
                logger.warning("knowledge seed: failed on '%s': %s", os.path.basename(path), e)

        if seeded:
            logger.info("knowledge seed: %d knowledge doc(s) ingested into '%s'", seeded, gk)
    except Exception:  # noqa: BLE001 — seeding must never break startup
        logger.exception("knowledge seed: unexpected failure; continuing boot")
