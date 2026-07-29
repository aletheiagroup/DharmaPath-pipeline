"""
dharmapath/storage/gcs_client.py

GCSClient — handles uploads to Google Cloud Storage.
Drop-in alternative to R2Client. Uses the same method signatures
so the pipeline runner can use either backend.

Resilience features:
  - Retries with exponential backoff on transient GCS errors
  - Idempotent uploads (skips if file already exists with matching size)
  - Structured logging

Requires: google-cloud-storage
Auth: uses Application Default Credentials (ADC) on GCE,
      or GOOGLE_APPLICATION_CREDENTIALS env var elsewhere.
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.cloud import storage
from google.api_core.exceptions import (
    ServiceUnavailable,
    InternalServerError,
    TooManyRequests,
    GatewayTimeout,
)

from config.settings import settings
from dharmapath.utils.retry import retry_sync

logger = logging.getLogger(__name__)

# Exceptions worth retrying on
_GCS_RETRYABLE = (
    ServiceUnavailable,
    InternalServerError,
    TooManyRequests,
    GatewayTimeout,
    ConnectionError,
    TimeoutError,
    OSError,
)


class GCSClient:
    """
    Client for Google Cloud Storage with retry and idempotency support.

    Same interface as R2Client for easy swapping in the pipeline runner.
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._storage_client = storage.Client(
            project=settings.gcp_project_id or None
        )
        self._bucket_name = bucket_name or settings.gcs_bucket_name
        self._bucket = self._storage_client.bucket(self._bucket_name)
        logger.info(f"GCSClient initialised for bucket: {self._bucket_name}")

    def _file_exists(self, gcs_key: str, local_size: int) -> bool:
        """Check if file already exists in GCS with matching size (idempotency)."""
        blob = self._bucket.blob(gcs_key)
        if blob.exists():
            blob.reload()
            remote_size = blob.size or 0
            if remote_size == local_size:
                logger.debug(f"File already exists in GCS: {gcs_key} ({remote_size} bytes)")
                return True
        return False

    def upload_file(self, local_path: str | Path, gcs_key: str) -> str:
        """
        Upload a single file with retries and idempotency.

        Retries: 3 attempts, 2s base backoff on transient GCS errors.
        Skips upload if file already exists with matching size.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")

        # Idempotency check
        local_size = path.stat().st_size
        if self._file_exists(gcs_key, local_size):
            logger.info(f"Skipping upload (already exists): {gcs_key}")
            return f"gs://{self._bucket_name}/{gcs_key}"

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
            blob = self._bucket.blob(gcs_key)
            blob.upload_from_filename(str(path), content_type=content_type)
            return f"gs://{self._bucket_name}/{gcs_key}"

        result = retry_sync(
            _do_upload,
            max_retries=3,
            base_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=_GCS_RETRYABLE,
            service="gcs",
            operation="upload_file",
            context={"key": gcs_key, "size": local_size},
        )

        logger.info(f"Uploaded {path.name} → gs://{self._bucket_name}/{gcs_key}")
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

    def download_file(self, gcs_key: str, local_path: str | Path) -> Path:
        """
        Download a file from GCS to local disk with retries.

        Retries: 3 attempts, 2s base backoff.
        """
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        def _do_download() -> Path:
            blob = self._bucket.blob(gcs_key)
            blob.download_to_filename(str(path))
            return path

        result = retry_sync(
            _do_download,
            max_retries=3,
            base_delay=2.0,
            retryable_exceptions=_GCS_RETRYABLE,
            service="gcs",
            operation="download_file",
            context={"key": gcs_key},
        )

        logger.info(f"Downloaded gs://{self._bucket_name}/{gcs_key} → {path}")
        return result

    def list_panels(self, chapter_id: str) -> list[str]:
        """List all panel image keys for a chapter."""
        prefix = f"{chapter_id}/panels/"
        blobs = self._storage_client.list_blobs(
            self._bucket_name, prefix=prefix
        )
        return [blob.name for blob in blobs if not blob.name.endswith("/")]

    def file_exists(self, gcs_key: str) -> bool:
        """Check if a file exists in GCS."""
        blob = self._bucket.blob(gcs_key)
        return blob.exists()
