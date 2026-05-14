"""
dharmapath/prompt_generator/generator.py

PromptGenerator — converts Panel + Chapter + CharacterRegistry
into ComfyUI-ready prompt dictionaries using Jinja2 templates
and the palette/style profile configs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dharmapath.models.screenplay import Chapter, Panel, Screenplay
from dharmapath.registry.registry import CharacterRegistry

logger = logging.getLogger(__name__)

# Config paths
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptGenerator:
    """
    Builds ComfyUI-ready prompt dicts from screenplay panels.

    Each prompt dict has the shape:
        {
            "positive": str,
            "negative": str,
            "controlnet_image": str | None,   # path to pose PNG if panel has pose_ref
        }
    """

    def __init__(self) -> None:
        self._palettes = self._load_json(_CONFIG_DIR / "palettes.json")
        self._style_profiles = self._load_json(_CONFIG_DIR / "style_profiles.json")

        self._jinja = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._positive_tmpl = self._jinja.get_template("positive_prompt.j2")
        self._negative_tmpl = self._jinja.get_template("negative_prompt.j2")

    # ── Public API ────────────────────────────────────────────

    def generate_panel_prompt(
        self,
        panel: Panel,
        chapter: Chapter,
        registry: CharacterRegistry,
    ) -> dict[str, str | None]:
        """
        Build a ComfyUI prompt dict for a single panel.

        Returns:
            {
                "positive": str,
                "negative": str,
                "controlnet_image": str | None,
            }
        """
        profile = self._get_style_profile(chapter.path.value)
        style_tags = profile.get("style_tags", []) + profile.get("quality_tags", [])

        # Resolve arc — prefer panel palette_override, fall back to chapter arc
        arc_key = (panel.palette_override or chapter.arc).value
        palette = self._palettes.get(arc_key, {})
        palette_tags = palette.get("tags", [])

        positive = self._positive_tmpl.render(
            panel=panel,
            chapter=chapter,
            registry=registry,
            style_tags=style_tags,
            palette_tags=palette_tags,
            quality_tags=[],  # already in style_tags
        ).strip()

        negative = self._negative_tmpl.render().strip()

        controlnet_image: str | None = None
        if panel.pose_ref:
            pose_path = Path("data/poses") / panel.pose_ref
            if pose_path.exists():
                controlnet_image = str(pose_path)
            else:
                logger.warning(
                    f"Panel {panel.panel_id}: pose_ref '{panel.pose_ref}' not found at {pose_path}. "
                    "ControlNet will be skipped for this panel."
                )

        logger.debug(f"Generated prompt for {panel.panel_id} "
                     f"(arc={arc_key}, chars={panel.characters})")

        return {
            "positive": positive,
            "negative": negative,
            "controlnet_image": controlnet_image,
        }

    def generate_batch(
        self,
        screenplay: Screenplay,
        registry: CharacterRegistry,
    ) -> list[dict[str, str | None]]:
        """
        Generate prompts for all panels in the screenplay, in order.

        Returns a list of prompt dicts aligned with screenplay.panels.
        """
        prompts = []
        for panel in screenplay.panels:
            prompt = self.generate_panel_prompt(panel, screenplay.chapter, registry)
            prompts.append(prompt)
        logger.info(
            f"Generated {len(prompts)} prompts for chapter "
            f"'{screenplay.chapter.chapter_id}'"
        )
        return prompts

    def generate_character_prompt(
        self,
        character_name: str,
        description: str,
        variation_idx: int,
        path: str = "itihaasa",
    ) -> dict[str, str]:
        """
        Build a candidate generation prompt for a character.
        Used by CharacterDesigner to generate 9 variations (3x3 grid).

        variation_idx: 0-8
            Row 0: frontal / three-quarter / profile
            Col 0: neutral / warm smile / intense expression
        """
        profile = self._get_style_profile(path)
        style_tags = profile.get("style_tags", []) + profile.get("quality_tags", [])

        lighting_variations = [
            "soft frontal lighting",
            "dramatic side lighting",
            "natural outdoor lighting",
        ]
        expression_variations = [
            "neutral dignified expression",
            "warm gentle smile",
            "intense focused expression",
        ]
        angle_variations = [
            "frontal view",
            "three-quarter view",
            "slight profile view",
        ]

        row = variation_idx // 3
        col = variation_idx % 3

        variation_tags = [
            lighting_variations[row],
            expression_variations[col],
            angle_variations[row],
            "character portrait",
            "upper body shot",
            "detailed face",
        ]

        positive = ", ".join(
            style_tags
            + [description]
            + variation_tags
        )

        negative = self._negative_tmpl.render().strip()

        return {"positive": positive, "negative": negative}

    # ── Internal helpers ──────────────────────────────────────

    def _get_style_profile(self, path_key: str) -> dict:
        profile = self._style_profiles.get(path_key)
        if not profile:
            logger.warning(f"No style profile for path '{path_key}'. Using itihaasa as fallback.")
            profile = self._style_profiles.get("itihaasa", {})
        return profile

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
