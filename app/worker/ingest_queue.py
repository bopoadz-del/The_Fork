from __future__ import annotations
import os

from arq import create_pool
from arq.connections import RedisSettings

_pool = None


async def enqueue_ingest(project_id: str, document_id: str, job_id: str) -> bool:
    global _pool
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return False
    try:
        if _pool is None:
            _pool = await create_pool(RedisSettings.from_dsn(redis_url))
        await _pool.enqueue_job("ingest_document", project_id, document_id, job_id)
        return True
    except Exception:
        return False
