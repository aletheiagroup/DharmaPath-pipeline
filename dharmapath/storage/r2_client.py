"""
dharmapath/storage/r2_client.py

R2Client — handles uploads to Cloudflare R2 via boto3 S3-compatible API.

Resilience features:
  - Retries with exponential backoff on transient S3/network errors
  - Idempotent uploads (skips if file already exists with matching size)
  - Structured logging
"""

from __future__ import annotations

import logging
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionError as BotoConnectionError

from config.settings import settings
from dharmapath.utils.retry import retry_sync

logger = logging.getLogger(__name__)

# Exceptions worth retrying on
_R2_RETRYABLE = (
    ClientError,  # Covers 5xx from S3/R2
    BotoConnectionError,
    ConnectionError,
    TimeoutError,
    OSError,
)


def _is_retryable_client_error(error: Exception) -> bool:
    """Check if a ClientError is transient (5xx) vs permanent (4xx)."""
    if isinstance(error, ClientError):
        code = int(error.response.get("Error", {}).get("Code", 0))
        return code >= 500 or code == 429
    return True


class R2Client:
    """
    Client for Cloudflare R2 storage with retry and idempotency support.
    """

    def __init__(self) -> None:
        self._s3 = boto3.client(
            service_name="s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",  # R2 expects 'auto'
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=120,
                retries={"max_attempts": 0},  # We handle retries ourselves
            ),
        )
        self._bucket = settings.r2_bucket_name

    def _file_exists(self, key: str, local_size: int) -> bool:
        """Check if file already exists in R2 with matching size (idempotency)."""
        try:
            response = self._s3.head_object(Bucket=self._bucket, Key=key)
            remote_size = response.get("ContentLength", 0)
            if remote_size == local_size:
                logger.debug(f"File already exists in R2: {key} ({remote_size} bytes)")
                return True
        except ClientError:
            pass  # File doesn't exist — that's fine
        return False

    def upload_file(self, local_path: str | Path, r2_key: str) -> str:
        """
        Upload a single file with retries and idempotency.

        Retries: 3 attempts, 2s base backoff on transient errors.
        Skips upload if file already exists with matching size.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")

        # Idempotency check — skip if already uploaded
        local_size = path.stat().st_size
        if self._file_exists(r2_key, local_size):
            logger.info(f"Skipping upload (already exists): {r2_key}")
            return f"r2://{self._bucket}/{r2_key}"

        # Determine content type
        suffix = path.suffix.lower()
        content_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".json": "application/json",
            ".webp": "image/webp",
        }
        content_type = content_type_map.get(suffix, "application/octet-stream")

        def _do_upload() -> str:
            self._s3.upload_file(
                str(path),
                self._bucket,
                r2_key,
                ExtraArgs={"ContentType": content_type},
            )
            return f"r2://{self._bucket}/{r2_key}"

        result = retry_sync(
            _do_upload,
            max_retries=3,
            base_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=_R2_RETRYABLE,
            service="r2",
            operation="upload_file",
            context={"key": r2_key, "size": local_size},
        )

        logger.info(f"Uploaded {path.name} to R2: {r2_key}")
        return result

    def upload_panel(self, local_path: str | Path, chapter_id: str) -> str:
        """Upload a panel image."""
        filename = Path(local_path).name
        key = f"{chapter_id}/panels/{filename}"
        return self.upload_file(local_path, key)

    def upload_batch(self, local_paths: list[str | Path], chapter_id: str) -> list[str]:
        """Upload a batch of panels."""
        return [self.upload_panel(path, chapter_id) for path in local_paths]

    def upload_episode(self, local_path: str | Path, chapter_id: str) -> str:
        """Upload a final episode file."""
        filename = Path(local_path).name
        key = f"{chapter_id}/final/{filename}"
        return self.upload_file(local_path, key)

    def upload_character_reference(self, local_path: str | Path, character_name: str) -> str:
        """Upload an approved character reference."""
        filename = Path(local_path).name
        key = f"characters/{character_name}/{filename}"
        return self.upload_file(local_path, key)
