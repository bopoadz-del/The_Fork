"""R2 / S3-compatible object storage for raw document archive.

Uploads raw Drive files to Cloudflare R2 so the platform no longer depends on
a local filesystem or a mounted Google Drive for source-of-truth binaries.
Environment variables (all required when archiving is enabled):

  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_ENDPOINT_URL        e.g. https://<account_id>.r2.cloudflarestorage.com
  R2_BUCKET_NAME
  R2_ACCOUNT_ID          (optional, stored in metadata for provenance)

If R2 credentials are missing, uploads gracefully degrade to a no-op so that
local/dev runs without object storage still work.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _log_r2_failure(message: str, *args: Any) -> None:
    """Log an expected R2 failure without a traceback.

    ``exc_info=True`` was leftover after #477: TIER-1 still printed a full
    botocore AccessDenied stack (run de6a06b7542c at 323/1380) next to
    'ingest continues', which looks like a crash and can trip log-based
    restarts. Never attach exception info here.
    """
    try:
        logger.warning(message, *args)
    except Exception as log_exc:
        # Logging must not become the abort. Print is the fallback.
        try:
            formatted = message % args if args else message
        except Exception:
            formatted = message
        print(
            f"[r2_storage] {formatted}; also failed to log "
            f"({type(log_exc).__name__}: {log_exc})",
            file=sys.stderr,
        )


def _client() -> Optional[Any]:
    """Return a boto3 S3 client if R2 credentials are configured.

    Never raises. Client construction AccessDenied (or any boto/botocore
    failure) returns ``None`` so callers cannot abort ingest.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        logger.warning("boto3 not installed; R2 archive disabled")
        return None

    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    endpoint = os.getenv("R2_ENDPOINT_URL")
    if not access_key or not secret_key or not endpoint:
        logger.debug("R2 credentials incomplete; archive disabled")
        return None

    try:
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    except Exception as exc:
        _log_r2_failure(
            "R2 client construction failed (%s: %s); archive disabled",
            type(exc).__name__,
            exc,
        )
        return None


def _bucket_name() -> Optional[str]:
    return os.getenv("R2_BUCKET_NAME")


def _object_key(project_id: str, drive_file_id: str, original_name: str) -> str:
    """Deterministic R2 object key: project/file-id/hash-of-name.ext"""
    name_hash = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
    safe_suffix = Path(original_name).suffix[:20]
    return f"projects/{project_id}/drive/{drive_file_id}/{name_hash}{safe_suffix}"


def object_key_for(project_id: str, drive_file_id: str, original_name: str) -> str:
    """Public wrapper for the P1B archive key layout.

    Preview reconstructs this when the row has ``drive_file_id`` but no
    stored ``r2_object_key`` (rag_render bulk ingest / failed archive).
    """
    return _object_key(project_id, drive_file_id, original_name)


def r2_configured() -> bool:
    """True when the process can actually GET an object (client + bucket)."""
    try:
        return bool(_client() and _bucket_name())
    except Exception as exc:
        _log_r2_failure(
            "R2 configured-check failed (%s: %s); treating as not configured",
            type(exc).__name__,
            exc,
        )
        return False


def fetch_failure_reason(
    object_key: str, *, bucket: Optional[str] = None,
) -> str:
    """Why ``fetch_object_bytes`` returned ``None`` — never a secret.

    #445 collapsed "R2 env missing", "wrong bucket", and "NoSuchKey" into
    the same silent None, so preview 404ed as "not available" with no
    operator signal. Call this only after a failed fetch.
    """
    key = (object_key or "").strip()
    if not key:
        return "R2 object key is empty"
    try:
        client = _client()
    except Exception as exc:
        _log_r2_failure(
            "R2 client check failed (%s: %s); treating as not configured",
            type(exc).__name__,
            exc,
        )
        return "R2 is not configured on this service"
    if client is None:
        return "R2 is not configured on this service"
    resolved = (bucket or _bucket_name() or "").strip()
    if not resolved:
        return "R2 bucket is not configured on this service"
    return "R2 object missing or fetch failed"


def _failed_archive(*, error: str, bucket: Optional[str] = None) -> Dict[str, Any]:
    """Canonical 'could not archive' payload. Callers must keep ingesting."""
    return {
        "archived": False,
        "r2_object_key": None,
        "r2_bucket": bucket,
        "r2_endpoint": os.getenv("R2_ENDPOINT_URL"),
        "r2_account_id": os.getenv("R2_ACCOUNT_ID"),
        "error": error,
    }


def archive_document(
    project_id: str,
    drive_file_id: str,
    original_name: str,
    raw_bytes: bytes,
    content_sha256: str,
) -> Dict[str, Any]:
    """Upload raw file bytes to R2 and return archive metadata.

    Never raises. PutObject AccessDenied (and any other boto/botocore failure)
    returns ``archived=False`` so TIER-1 ingest can still write the Neon row.

    Returns a dict with:
      - archived: bool
      - r2_object_key: str | None
      - r2_bucket: str | None
      - r2_endpoint: str | None
      - r2_account_id: str | None
      - error: str | None
    """
    bucket: Optional[str] = None
    try:
        bucket = _bucket_name()
        # Client construction used to sit *outside* the try. boto3.client()
        # rarely raises AccessDenied, but a credential/config error here used
        # to abort the whole P1B run before the document reached Neon.
        s3 = _client()
        if not s3 or not bucket:
            return {
                "archived": False,
                "r2_object_key": None,
                "r2_bucket": None,
                "r2_endpoint": None,
                "r2_account_id": None,
                "error": "R2_NOT_CONFIGURED",
            }

        key = _object_key(project_id, drive_file_id, original_name)
        # AWS S3/R2 expect the SHA256 checksum base64-encoded, not hex.
        checksum_b64 = base64.b64encode(bytes.fromhex(content_sha256)).decode("ascii")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=raw_bytes,
            ChecksumSHA256=checksum_b64,
            Metadata={
                "drive-file-id": drive_file_id,
                "content-sha256": content_sha256,
            },
        )
        return {
            "archived": True,
            "r2_object_key": key,
            "r2_bucket": bucket,
            "r2_endpoint": os.getenv("R2_ENDPOINT_URL"),
            "r2_account_id": os.getenv("R2_ACCOUNT_ID"),
            "error": None,
        }
    except Exception as exc:  # archive must never kill ingestion
        _log_r2_failure(
            "R2 archive failed for %s (%s: %s); ingest continues without object storage",
            original_name,
            type(exc).__name__,
            exc,
        )
        return _failed_archive(
            error=f"R2_UPLOAD_FAILED: {type(exc).__name__}: {exc}",
            bucket=bucket,
        )


def fetch_object_bytes(
    object_key: str, bucket: Optional[str] = None,
) -> Optional[bytes]:
    """Download one archived object. ``None`` if R2 is off or the key is missing.

    P1B Drive ingest archives the raw bytes here and then deletes the local
    copy (``delete_local_archive``). Preview / download must follow this key
    — a document row with chunks but no disk file is the normal corpus shape,
    not a missing document.

    ``bucket`` overrides ``R2_BUCKET_NAME`` so a row that recorded
    ``metadata.r2_bucket`` at ingest time still resolves if the process
    default is unset or differs.
    """
    key = (object_key or "").strip()
    if not key:
        return None
    resolved_bucket = (bucket or _bucket_name() or "").strip()
    try:
        s3 = _client()
        if not s3 or not resolved_bucket:
            return None
        resp = s3.get_object(Bucket=resolved_bucket, Key=key)
        body = resp["Body"].read()
        return body if body is not None else b""
    except Exception as exc:
        _log_r2_failure(
            "R2 get_object failed for %s (%s: %s)",
            key,
            type(exc).__name__,
            exc,
        )
        return None


def delete_local_archive(path: str) -> None:
    """Remove the local temp copy after successful R2 upload + indexing."""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete local temp file %s: %s", path, exc)
