"""
dharmapath/genai/gemini_client.py

GeminiClient — wraps the Google GenAI SDK (google-genai) to provide
LLM-powered features for the DharmaPath pipeline:

  • Prompt refinement — improve ComfyUI positive prompts for better outputs
  • Panel QC review — vision-based quality check of generated panel images
  • Screenplay drafting — generate structured screenplay JSON from narrative text

Resilience features:
  - Retries with exponential backoff on 429/503/timeout
  - Circuit breaker to prevent hammering Gemini during outages
  - Explicit timeout budgets per operation
  - Structured JSON logging on all retry events

Uses Gemini model from settings (configurable via GEMINI_MODEL env var).
Authentication: uses GCP Application Default Credentials on GCE,
or GOOGLE_API_KEY env var elsewhere.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from config.settings import settings
from dharmapath.utils.retry import retry_sync, CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# ── Circuit breaker for Gemini (shared across all GeminiClient instances) ────
_gemini_breaker = CircuitBreaker(
    service="gemini",
    failure_threshold=5,
    reset_after_s=60.0,
)

# ── Retryable exceptions ────────────────────────────────────────────────────
# google-genai wraps API errors in its own exception types.
# We also catch standard connection errors.
try:
    from google.api_core.exceptions import (
        ResourceExhausted,      # 429 Rate limit
        ServiceUnavailable,     # 503
        DeadlineExceeded,       # Timeout
        InternalServerError,    # 500
        TooManyRequests,        # 429 (alias)
    )
    _GEMINI_RETRYABLE = (
        ResourceExhausted,
        ServiceUnavailable,
        DeadlineExceeded,
        InternalServerError,
        TooManyRequests,
        ConnectionError,
        TimeoutError,
    )
except ImportError:
    # Fallback if google-api-core not installed alongside google-genai
    _GEMINI_RETRYABLE = (ConnectionError, TimeoutError, OSError)

# ── System prompts ────────────────────────────────────────────────────────────

_PROMPT_REFINE_SYSTEM = """You are an expert Stable Diffusion / ComfyUI prompt engineer specialising
in manhwa (Korean webcomic / Webtoon) art. Your task is to take a raw positive
prompt and refine it for maximum visual quality.

Rules:
- Keep the manhwa / webtoon art style tags.
- Preserve ALL character descriptions and scene details — do not remove information.
- Improve tag ordering: style tags first, then subject, then composition, then quality.
- Add helpful tags for better generation (e.g. lighting descriptors, composition terms).
- Remove redundant or conflicting tags.
- Keep the prompt under 200 words.
- Output ONLY the refined prompt text, nothing else. No explanation."""

_PANEL_QC_SYSTEM = """You are a visual quality inspector for a manhwa (Webtoon) production pipeline.
You will receive a generated panel image along with metadata about what the panel
should depict.

Evaluate the image on these criteria and return a JSON object:
{
  "overall_score": <1-10>,
  "anatomy_score": <1-10>,
  "style_consistency": <1-10>,
  "composition_score": <1-10>,
  "character_accuracy": <1-10>,
  "issues": ["list of specific issues found"],
  "recommendation": "approve" | "flag_for_review" | "regenerate",
  "notes": "brief explanation"
}

Be strict about anatomy (hands, faces, proportions) and style consistency.
A score below 6 on any criterion should result in "flag_for_review" or "regenerate"."""

_SCREENPLAY_SYSTEM = """You are a manhwa screenplay writer for DharmaPath, a Webtoon series
retelling Indian epics. You generate structured JSON screenplay files.

The JSON must conform to this schema:
- version: "1.0"
- chapter: {chapter_id, path, arc, title, description, arc_number, lesson_id?}
- panels: array of 35-60 panels, each with:
  panel_id (p01-p60), size (full/half/quarter), beat, shot_type,
  characters[], action, environment, lighting, camera, mood,
  dialogue[], pose_ref?, palette_override?, human_required

Rules:
- p01 must have beat=hook
- Exactly one beat=impact panel (must be size=full, human_required=true)
- Exactly one beat=quiet panel (max 10 words total dialogue)
- Last panel must have beat=close
- No dialogue entry may exceed 25 words
- Quarter panels must appear in groups of exactly 4
- At least 3 thought-bubble (type=thought) entries across the chapter
- Output valid JSON only, no markdown fencing."""


class GeminiClient:
    """
    Client for Google Gemini API with built-in resilience.

    Uses the official google-genai SDK. Model is configurable via
    settings.gemini_model (GEMINI_MODEL env var).

    Features:
      - Retries with exponential backoff on rate limits and transient errors
      - Circuit breaker (shared across instances) prevents hammering during outages
      - Structured JSON logging for all retry events

    Usage:
        client = GeminiClient()
        refined = client.refine_prompt("raw prompt text")
        qc = client.review_panel("path/to/image.png", panel_metadata)
    """

    def __init__(self, model: str | None = None) -> None:
        """
        Initialise the Gemini client.

        Args:
            model: Gemini model name. Defaults to settings.gemini_model
                   (usually 'gemini-2.0-flash').
        """
        self._model = model or settings.gemini_model

        # Initialise the client — uses GOOGLE_API_KEY env var or
        # Application Default Credentials (ADC) on GCE
        client_kwargs: dict[str, Any] = {}
        if settings.google_api_key:
            client_kwargs["api_key"] = settings.google_api_key
        elif settings.gcp_project_id:
            client_kwargs["project"] = settings.gcp_project_id
            client_kwargs["location"] = settings.gcp_region

        self._client = genai.Client(**client_kwargs)

        logger.info(f"GeminiClient initialised with model={self._model}")

    # ── Prompt Refinement ────────────────────────────────────────

    def refine_prompt(self, raw_prompt: str) -> str:
        """
        Refine a raw ComfyUI positive prompt using Gemini.

        Retries: 3 attempts, 1s base backoff on 429/503/timeout.
        """
        def _do_refine() -> str:
            response = self._client.models.generate_content(
                model=self._model,
                contents=raw_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_PROMPT_REFINE_SYSTEM,
                    temperature=0.7,
                    max_output_tokens=512,
                ),
            )
            return response.text.strip()

        refined = retry_sync(
            _do_refine,
            max_retries=3,
            base_delay=1.0,
            backoff_factor=2.0,
            retryable_exceptions=_GEMINI_RETRYABLE,
            service="gemini",
            operation="refine_prompt",
            context={"prompt_len": len(raw_prompt)},
        )

        logger.info(f"Prompt refined: {len(raw_prompt)} → {len(refined)} chars")
        return refined

    def refine_batch(self, prompts: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
        """
        Refine the positive prompt in each prompt dict.

        Uses circuit breaker — if Gemini is down, skips refinement
        and returns original prompts rather than blocking the pipeline.
        """
        refined = []
        for i, prompt_dict in enumerate(prompts):
            try:
                if _gemini_breaker.is_open:
                    logger.warning(
                        f"Gemini circuit breaker open — skipping refinement "
                        f"for prompt {i + 1}/{len(prompts)}"
                    )
                    refined.append(prompt_dict)
                    continue

                new_positive = self.refine_prompt(prompt_dict["positive"])
                _gemini_breaker.record_success()
                refined.append({
                    **prompt_dict,
                    "positive": new_positive,
                })
                logger.debug(f"Refined prompt {i + 1}/{len(prompts)}")

            except _GEMINI_RETRYABLE as e:
                _gemini_breaker.record_failure()
                logger.warning(
                    f"Failed to refine prompt {i + 1} after retries: {e}. Using original."
                )
                refined.append(prompt_dict)

            except Exception as e:
                logger.warning(
                    f"Unexpected error refining prompt {i + 1}: {e}. Using original."
                )
                refined.append(prompt_dict)

        return refined

    # ── Panel QC Review (Vision) ─────────────────────────────────

    def review_panel(
        self,
        image_path: str | Path,
        panel_metadata: dict | None = None,
    ) -> dict:
        """
        Use Gemini Vision to QC a generated panel image.

        Retries: 3 attempts, 2s base backoff on 429/503/timeout.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Panel image not found: {path}")

        image_bytes = path.read_bytes()
        mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"

        def _do_review() -> dict:
            parts = []

            if panel_metadata:
                context = (
                    f"Panel metadata:\n"
                    f"  Action: {panel_metadata.get('action', 'N/A')}\n"
                    f"  Characters: {panel_metadata.get('characters', [])}\n"
                    f"  Shot type: {panel_metadata.get('shot_type', 'N/A')}\n"
                    f"  Mood: {panel_metadata.get('mood', 'N/A')}\n"
                    f"  Environment: {panel_metadata.get('environment', 'N/A')}\n"
                    f"\nPlease evaluate the image against this intended content."
                )
                parts.append(types.Part.from_text(text=context))

            parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

            response = self._client.models.generate_content(
                model=self._model,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    system_instruction=_PANEL_QC_SYSTEM,
                    temperature=0.3,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )

            try:
                return json.loads(response.text)
            except json.JSONDecodeError:
                logger.warning("Gemini QC response was not valid JSON, wrapping.")
                return {
                    "overall_score": 0,
                    "recommendation": "flag_for_review",
                    "notes": response.text,
                    "issues": ["Could not parse structured QC response"],
                }

        result = retry_sync(
            _do_review,
            max_retries=3,
            base_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=_GEMINI_RETRYABLE,
            service="gemini",
            operation="review_panel",
            context={"image": path.name},
        )

        logger.info(
            f"Panel QC for {path.name}: "
            f"score={result.get('overall_score')}, "
            f"rec={result.get('recommendation')}"
        )
        return result

    def review_batch(
        self,
        image_paths: list[str | Path],
        panel_metadata_list: list[dict] | None = None,
    ) -> list[dict]:
        """QC review multiple panel images with circuit breaker protection."""
        results = []
        metadata_list = panel_metadata_list or [None] * len(image_paths)

        for i, (img_path, metadata) in enumerate(zip(image_paths, metadata_list)):
            try:
                if _gemini_breaker.is_open:
                    logger.warning(
                        f"Gemini circuit breaker open — skipping QC for image {i + 1}"
                    )
                    results.append({
                        "overall_score": 0,
                        "recommendation": "flag_for_review",
                        "issues": ["Gemini QC skipped — circuit breaker open"],
                        "notes": "Manual review required.",
                    })
                    continue

                result = self.review_panel(img_path, metadata)
                _gemini_breaker.record_success()
                results.append(result)

            except Exception as e:
                _gemini_breaker.record_failure()
                logger.error(f"QC review failed for {img_path}: {e}")
                results.append({
                    "overall_score": 0,
                    "recommendation": "flag_for_review",
                    "issues": [f"Review failed: {e}"],
                    "notes": "Automated review could not be completed.",
                })

        return results

    # ── Screenplay Generation ────────────────────────────────────

    def generate_screenplay_draft(
        self,
        narrative: str,
        path: str = "itihaasa",
        arc: str = "conflict",
        chapter_number: int = 1,
    ) -> dict:
        """
        Generate a structured screenplay JSON from a narrative description.

        Retries: 2 attempts, 3s base backoff.
        Also retries on JSONDecodeError (Gemini sometimes returns partial JSON).
        """
        user_prompt = (
            f"Generate a manhwa screenplay for the following narrative.\n\n"
            f"Path: {path}\n"
            f"Arc: {arc}\n"
            f"Chapter number: {chapter_number}\n"
            f"Chapter ID: {path}_ch{chapter_number:02d}\n\n"
            f"Narrative:\n{narrative}\n\n"
            f"Generate the complete screenplay JSON with 40-50 panels."
        )

        def _do_generate() -> dict:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SCREENPLAY_SYSTEM,
                    temperature=0.8,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )

            # This can raise JSONDecodeError which we want to retry
            return json.loads(response.text)

        logger.info(f"Generating screenplay draft for {path}_ch{chapter_number:02d}")

        screenplay_data = retry_sync(
            _do_generate,
            max_retries=2,
            base_delay=3.0,
            backoff_factor=2.0,
            retryable_exceptions=(*_GEMINI_RETRYABLE, json.JSONDecodeError),
            service="gemini",
            operation="generate_screenplay",
            context={"path": path, "chapter": chapter_number},
        )

        logger.info(
            f"Screenplay draft generated: "
            f"{len(screenplay_data.get('panels', []))} panels"
        )
        return screenplay_data
