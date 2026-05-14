"""
dharmapath/comfyui/workflow_builder.py

WorkflowBuilder — injects prompt parameters into ComfyUI workflow JSON templates.
Knows the node IDs of the standard DharmaPath workflow graph.
"""

from __future__ import annotations

import copy
import json
import logging
import random
from pathlib import Path

from dharmapath.models.screenplay import Chapter, Panel

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent / "workflows"

# ── Node ID constants ─────────────────────────────────────────────────────────
# These match the node IDs in our ComfyUI workflow JSON templates.
# Update these if the workflow graph changes on RunPod.

class NodeID:
    # panel_generation.json nodes
    POSITIVE_CLIP     = "6"    # CLIPTextEncode — positive prompt
    NEGATIVE_CLIP     = "7"    # CLIPTextEncode — negative prompt
    KSAMPLER          = "3"    # KSampler — main generation
    LORA_LOADER       = "10"   # LoraLoader — style LoRA
    CHAR_LORA_LOADER  = "11"   # LoraLoader — character LoRA (chained)
    IP_ADAPTER        = "15"   # IPAdapter — face reference node
    CONTROLNET_APPLY  = "20"   # ControlNetApply — DWPose skeleton
    CONTROLNET_LOAD   = "21"   # ControlNetLoader
    LOAD_IMAGE_POSE   = "22"   # LoadImage — pose skeleton input
    LOAD_IMAGE_FACE   = "23"   # LoadImage — IP-Adapter face crop input
    ESRGAN_UPSCALE    = "30"   # ImageUpscaleWithModel — ESRGAN x2
    SAVE_IMAGE        = "35"   # SaveImage — final output
    COLOR_GRADING     = "40"   # CLIPVisionEncode repurposed as colour grade select

    # character_candidates.json nodes
    CAND_POSITIVE     = "6"
    CAND_NEGATIVE     = "7"
    CAND_KSAMPLER     = "3"
    CAND_LORA_LOADER  = "10"
    CAND_SAVE_IMAGE   = "35"

    # inpaint.json nodes
    INPAINT_POSITIVE  = "6"
    INPAINT_NEGATIVE  = "7"
    INPAINT_KSAMPLER  = "3"
    INPAINT_MASK      = "50"   # LoadImageMask
    INPAINT_DENOISE   = 0.65   # Fixed denoise strength for inpainting


# Arc → colour-grade node selection value
ARC_COLOR_GRADE_MAP = {
    "divine":   1,
    "conflict": 2,
    "domestic": 3,
    "lesson":   4,
}


class WorkflowBuilder:
    """
    Injects panel-specific parameters into ComfyUI workflow JSON templates.

    Workflow templates live in dharmapath/comfyui/workflows/ as JSON files.
    The builder deep-copies the template and patches only the relevant nodes.
    """

    def __init__(self) -> None:
        self._panel_wf = self._load_workflow("panel_generation.json")
        self._candidate_wf = self._load_workflow("character_candidates.json")
        self._inpaint_wf = self._load_workflow("inpaint.json")

    # ── Public builders ───────────────────────────────────────

    def build_panel_workflow(
        self,
        prompt: dict[str, str | None],
        panel: Panel,
        chapter: Chapter,
        style_profile: dict,
        character_face_paths: dict[str, str],  # {character_name: face_crop_path}
        character_ip_weights: dict[str, float],  # {character_name: ip_adapter_weight}
    ) -> dict:
        """
        Build the ComfyUI workflow for generating a single panel.

        Args:
            prompt: Output of PromptGenerator.generate_panel_prompt()
            panel: Panel model
            chapter: Chapter model
            style_profile: From config/style_profiles.json for chapter.path
            character_face_paths: {name: face_crop_path} for IP-Adapter
            character_ip_weights: {name: float} for IP-Adapter weight

        Returns:
            Fully populated workflow dict ready to POST to /prompt
        """
        wf = copy.deepcopy(self._panel_wf)

        # ── Prompts ───────────────────────────────────────────
        self._set_node_input(wf, NodeID.POSITIVE_CLIP, "text", prompt["positive"])
        self._set_node_input(wf, NodeID.NEGATIVE_CLIP, "text", prompt["negative"])

        # ── Sampler settings ──────────────────────────────────
        seed = random.randint(0, 2**32 - 1)
        self._set_node_inputs(wf, NodeID.KSAMPLER, {
            "seed":       seed,
            "steps":      style_profile.get("steps", 30),
            "cfg":        style_profile.get("cfg", 7.0),
            "sampler_name": style_profile.get("sampler", "dpmpp_2m_karras"),
            "scheduler":  "karras",
        })

        # ── Style LoRA ────────────────────────────────────────
        lora_model = style_profile.get("lora_model", "")
        lora_weight = style_profile.get("lora_weight", 0.85)
        if lora_model:
            self._set_node_inputs(wf, NodeID.LORA_LOADER, {
                "lora_name":       lora_model,
                "strength_model":  lora_weight,
                "strength_clip":   lora_weight,
            })

        # ── IP-Adapter face reference ─────────────────────────
        # Use first approved character's face crop (primary character)
        primary_face_path = None
        primary_ip_weight = style_profile.get("ip_adapter_weight", 0.65)
        if character_face_paths:
            primary_name = list(character_face_paths.keys())[0]
            primary_face_path = character_face_paths[primary_name]
            primary_ip_weight = character_ip_weights.get(primary_name, primary_ip_weight)

        if primary_face_path:
            self._set_node_input(wf, NodeID.LOAD_IMAGE_FACE, "image", primary_face_path)
            self._set_node_input(wf, NodeID.IP_ADAPTER, "weight", primary_ip_weight)
        else:
            # Disable IP-Adapter node if no face reference
            self._disable_node(wf, NodeID.IP_ADAPTER)

        # ── ControlNet (DWPose) ───────────────────────────────
        if prompt.get("controlnet_image"):
            self._set_node_input(
                wf, NodeID.LOAD_IMAGE_POSE, "image", prompt["controlnet_image"]
            )
        else:
            self._disable_node(wf, NodeID.CONTROLNET_APPLY)
            self._disable_node(wf, NodeID.LOAD_IMAGE_POSE)

        # ── Colour grading arc ────────────────────────────────
        arc_key = (panel.palette_override or chapter.arc).value
        color_grade_idx = ARC_COLOR_GRADE_MAP.get(arc_key, 1)
        self._set_node_input(wf, NodeID.COLOR_GRADING, "mode", color_grade_idx)

        # ── Output filename ───────────────────────────────────
        chapter_num = chapter.chapter_id.split("ch")[-1] if "ch" in chapter.chapter_id else "00"
        panel_num = panel.panel_id.lstrip("p")
        filename_prefix = f"dp_ch{chapter_num}_p{panel_num}_v1"
        self._set_node_input(wf, NodeID.SAVE_IMAGE, "filename_prefix", filename_prefix)

        logger.debug(
            f"Built panel workflow for {panel.panel_id} "
            f"(seed={seed}, arc={arc_key}, controlnet={'yes' if prompt.get('controlnet_image') else 'no'})"
        )
        return wf

    def build_candidate_workflow(
        self,
        character_name: str,
        description: str,
        variation_idx: int,
        style_profile: dict,
        prompt: dict[str, str],
    ) -> dict:
        """
        Build a ComfyUI workflow for generating one character candidate image.

        Args:
            character_name: Used for output filename prefix
            description: Character visual description
            variation_idx: 0-8 (determines lighting/expression/angle variation)
            style_profile: From config/style_profiles.json
            prompt: Output of PromptGenerator.generate_character_prompt()

        Returns:
            Workflow dict ready to POST to /prompt
        """
        wf = copy.deepcopy(self._candidate_wf)

        self._set_node_input(wf, NodeID.CAND_POSITIVE, "text", prompt["positive"])
        self._set_node_input(wf, NodeID.CAND_NEGATIVE, "text", prompt["negative"])

        seed = random.randint(0, 2**32 - 1)
        self._set_node_inputs(wf, NodeID.CAND_KSAMPLER, {
            "seed":       seed,
            "steps":      style_profile.get("steps", 30),
            "cfg":        style_profile.get("cfg", 7.0),
            "sampler_name": style_profile.get("sampler", "dpmpp_2m_karras"),
            "scheduler":  "karras",
        })

        lora_model = style_profile.get("lora_model", "")
        if lora_model:
            self._set_node_inputs(wf, NodeID.CAND_LORA_LOADER, {
                "lora_name":      lora_model,
                "strength_model": style_profile.get("lora_weight", 0.85),
                "strength_clip":  style_profile.get("lora_weight", 0.85),
            })

        safe_name = character_name.lower().replace(" ", "_")
        filename_prefix = f"candidate_{safe_name}_{variation_idx + 1:02d}"
        self._set_node_input(wf, NodeID.CAND_SAVE_IMAGE, "filename_prefix", filename_prefix)

        logger.debug(
            f"Built candidate workflow for '{character_name}' "
            f"variation {variation_idx + 1}/9 (seed={seed})"
        )
        return wf

    def build_inpaint_workflow(
        self,
        prompt: dict[str, str],
        panel_image_path: str,
        mask_path: str,
        style_profile: dict,
    ) -> dict:
        """
        Build an inpainting workflow for correcting a flagged panel.

        Args:
            prompt: Prompt dict for the correction region
            panel_image_path: Path to the original generated panel
            mask_path: Path to the inpainting mask (white = repaint area)
            style_profile: Style settings for sampler config

        Returns:
            Inpaint workflow dict ready to POST to /prompt
        """
        wf = copy.deepcopy(self._inpaint_wf)

        self._set_node_input(wf, NodeID.INPAINT_POSITIVE, "text", prompt["positive"])
        self._set_node_input(wf, NodeID.INPAINT_NEGATIVE, "text", prompt["negative"])
        self._set_node_inputs(wf, NodeID.INPAINT_KSAMPLER, {
            "seed":         random.randint(0, 2**32 - 1),
            "steps":        style_profile.get("steps", 30),
            "cfg":          style_profile.get("cfg", 7.0),
            "sampler_name": style_profile.get("sampler", "dpmpp_2m_karras"),
            "scheduler":    "karras",
            "denoise":      NodeID.INPAINT_DENOISE,
        })
        self._set_node_input(wf, NodeID.INPAINT_MASK, "image", mask_path)

        return wf

    # ── Internal helpers ──────────────────────────────────────

    @staticmethod
    def _load_workflow(filename: str) -> dict:
        path = _WORKFLOWS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Workflow template not found: {path}. "
                "Export your ComfyUI workflow as JSON and save it here."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _set_node_input(wf: dict, node_id: str, input_key: str, value) -> None:
        """Set a single input field on a ComfyUI workflow node."""
        node = wf.get(node_id)
        if node is None:
            logger.warning(f"Node '{node_id}' not found in workflow — skipping.")
            return
        node.setdefault("inputs", {})[input_key] = value

    @staticmethod
    def _set_node_inputs(wf: dict, node_id: str, inputs: dict) -> None:
        """Set multiple input fields on a node at once."""
        node = wf.get(node_id)
        if node is None:
            logger.warning(f"Node '{node_id}' not found in workflow — skipping.")
            return
        node.setdefault("inputs", {}).update(inputs)

    @staticmethod
    def _disable_node(wf: dict, node_id: str) -> None:
        """
        Mark a node as muted/bypassed by setting _meta.mode=4.
        ComfyUI treats mode=4 as 'bypass'.
        """
        node = wf.get(node_id)
        if node is None:
            return
        node.setdefault("_meta", {})["mode"] = 4
        logger.debug(f"Disabled/bypassed node '{node_id}'")
