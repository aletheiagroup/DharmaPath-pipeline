"""
dharmapath/storage/r2_client.py

R2Client — handles uploads to Cloudflare R2 via boto3 S3-compatible API.
"""

import logging
from pathlib import Path
import boto3
from botocore.config import Config
from config.settings import settings

logger = logging.getLogger(__name__)

class R2Client:
    """
    Client for Cloudflare R2 storage.
    """

    def __init__(self):
        self._s3 = boto3.client(
            service_name="s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",  # R2 expects 'auto'
            config=Config(signature_version="s3v4")
        )
        self._bucket = settings.r2_bucket_name

    def upload_file(self, local_path: str | Path, r2_key: str) -> str:
        """
        Uploads a single file and returns its public URL (if configured) or the R2 key.
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")

        logger.info(f"Uploading {path.name} to R2: {r2_key}")
        
        # Determine content type
        content_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        if path.suffix.lower() == ".json":
            content_type = "application/json"

        self._s3.upload_file(
            str(path),
            self._bucket,
            r2_key,
            ExtraArgs={"ContentType": content_type}
        )
        
        # Return the R2 key or a simulated URL
        return f"r2://{self._bucket}/{r2_key}"

    def upload_panel(self, local_path: str | Path, chapter_id: str) -> str:
        """Uploads a panel image."""
        filename = Path(local_path).name
        key = f"{chapter_id}/panels/{filename}"
        return self.upload_file(local_path, key)

    def upload_batch(self, local_paths: list[str | Path], chapter_id: str) -> list[str]:
        """Uploads a batch of panels."""
        urls = []
        for path in local_paths:
            urls.append(self.upload_panel(path, chapter_id))
        return urls

    def upload_episode(self, local_path: str | Path, chapter_id: str) -> str:
        """Uploads a final episode file."""
        filename = Path(local_path).name
        key = f"{chapter_id}/final/{filename}"
        return self.upload_file(local_path, key)

    def upload_character_reference(self, local_path: str | Path, character_name: str) -> str:
        """Uploads an approved character reference."""
        filename = Path(local_path).name
        key = f"characters/{character_name}/{filename}"
        return self.upload_file(local_path, key)
