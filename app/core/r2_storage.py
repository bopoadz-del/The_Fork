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
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _client() -> Optional[Any]:
    """Return a boto3 S3 client if R2 credentials are configured."""
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

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _bucket_name() -> Optional[str]:
    return os.getenv("R2_BUCKET_NAME")


def _object_key(project_id: str, drive_file_id: str, original_name: str) -> str:
    """Deterministic R2 object key: project/file-id/hash-of-name.ext"""
    name_hash = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
    safe_suffix = Path(original_name).suffix[:20]
    return f"projects/{project_id}/drive/{drive_file_id}/{name_hash}{safe_suffix}"


def archive_document(
    project_id: str,
    drive_file_id: str,
    original_name: str,
    raw_bytes: bytes,
    content_sha256: str,
) -> Dict[str, Any]:
    """Upload raw file bytes to R2 and return archive metadata.

    Returns a dict with:
      - archived: bool
      - r2_object_key: str | None
      - r2_bucket: str | None
      - r2_endpoint: str | None
      - r2_account_id: str | None
      - error: str | None
    """
    bucket = _bucket_name()
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
    try:
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
    except Exception as exc:  # noqa: BLE001 — archive must never kill ingestion
        logger.exception("R2 upload failed for %s", original_name)
        return {
            "archived": False,
            "r2_object_key": None,
            "r2_bucket": bucket,
            "r2_endpoint": os.getenv("R2_ENDPOINT_URL"),
            "r2_account_id": os.getenv("R2_ACCOUNT_ID"),
            "error": f"R2_UPLOAD_FAILED: {type(exc).__name__}: {exc}",
        }


def delete_local_archive(path: str) -> None:
    """Remove the local temp copy after successful R2 upload + indexing."""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete local temp file %s: %s", path, exc)
