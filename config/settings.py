"""
config/settings.py

Loads all environment variables from .env and exposes a typed Settings object.
All other modules import `settings` from here — never read os.environ directly.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── ComfyUI (GCE VM or RunPod) ───────────────────────────
    comfyui_base_url: str = "http://localhost:8188"
    runpod_api_key: str = ""

    # ── Google Cloud Platform ─────────────────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "asia-south1"

    # ── Gemini API ────────────────────────────────────────────
    gemini_model: str = "gemini-2.0-flash"
    google_api_key: str = ""  # Optional: uses ADC on GCE if empty

    # ── Google Cloud Storage (optional, alternative to R2) ────
    gcs_bucket_name: str = "dharmapath"

    # ── Cloudflare R2 (legacy — still supported) ──────────────
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "dharmapath"
    r2_endpoint_url: str = ""

    # ── Directory paths ───────────────────────────────────────
    screenplays_dir: Path = Path("data/screenplays")
    outputs_dir: Path = Path("data/outputs")
    characters_dir: Path = Path("data/characters")
    characters_full_dir: Path = Path("data/characters_full")
    candidates_dir: Path = Path("data/candidates")
    poses_dir: Path = Path("data/poses")

    # ── Pipeline behaviour ────────────────────────────────────
    r2_batch_upload_interval: int = 5

    # ── Web server ────────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    app_env: str = "development"

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"

    @field_validator("r2_endpoint_url", mode="before")
    @classmethod
    def build_r2_endpoint(cls, v: str, info) -> str:
        """Auto-build R2 endpoint from account_id if not explicitly set."""
        if v:
            return v
        account_id = info.data.get("r2_account_id", "")
        if account_id:
            return f"https://{account_id}.r2.cloudflarestorage.com"
        return ""

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def comfyui_prompt_url(self) -> str:
        return f"{self.comfyui_base_url.rstrip('/')}/prompt"

    @property
    def comfyui_history_url(self) -> str:
        return f"{self.comfyui_base_url.rstrip('/')}/history"

    @property
    def comfyui_view_url(self) -> str:
        return f"{self.comfyui_base_url.rstrip('/')}/view"

    @property
    def comfyui_system_stats_url(self) -> str:
        return f"{self.comfyui_base_url.rstrip('/')}/system_stats"


# Singleton — import this everywhere
settings = Settings()
