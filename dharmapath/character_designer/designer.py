"""
dharmapath/character_designer/designer.py

CharacterDesigner — handles candidate generation for characters
and the approval process (including face cropping).
"""

import logging
from pathlib import Path
from PIL import Image
from dharmapath.comfyui.client import ComfyUIClient
from dharmapath.comfyui.workflow_builder import WorkflowBuilder
from dharmapath.prompt_generator.generator import PromptGenerator
from dharmapath.registry.registry import CharacterRegistry
from config.settings import settings

logger = logging.getLogger(__name__)

class CharacterDesigner:
    """
    Handles character candidate generation and selection.
    """

    def __init__(self):
        self._workflow_builder = WorkflowBuilder()
        self._prompt_generator = PromptGenerator()

    async def generate_candidates(
        self,
        character_name: str,
        description: str,
        comfyui: ComfyUIClient,
        registry: CharacterRegistry,
        path: str = "itihaasa"
    ) -> list[str]:
        """
        Generates 9 candidate images for a character.
        Saves to data/candidates/{character_name}/candidate_{01-09}.png
        """
        candidate_dir = settings.candidates_dir / character_name
        candidate_dir.mkdir(parents=True, exist_ok=True)
        
        # Get style profile for lora settings
        style_profile = self._prompt_generator._get_style_profile(path)
        
        candidate_paths = []
        
        for i in range(9):
            prompt = self._prompt_generator.generate_character_prompt(character_name, description, i, path)
            workflow = self._workflow_builder.build_candidate_workflow(
                character_name, description, i, style_profile, prompt
            )
            
            save_path = candidate_dir / f"candidate_{i+1:02d}.png"
            await comfyui.generate_panel(workflow, str(save_path))
            candidate_paths.append(str(save_path))
            
        registry.set_candidates(character_name, "default", candidate_paths)
        registry.save()
        
        return candidate_paths

    def approve_candidate(
        self,
        character_name: str,
        state: str,
        candidate_path: str,
        registry: CharacterRegistry
    ) -> str:
        """
        Approves a candidate image, crops the face, and saves to registry.
        """
        # Load candidate image
        img = Image.open(candidate_path)
        
        # Perform face crop
        face_crop = self._face_crop(img)
        
        # Define paths
        face_crop_filename = f"{character_name}_{state}.jpg"
        full_image_filename = f"{character_name}_{state}_full.jpg"
        
        face_crop_path = settings.characters_dir / face_crop_filename
        full_image_path = settings.characters_full_dir / full_image_filename
        
        # Save images
        face_crop.save(face_crop_path, "JPEG", quality=95)
        img.convert("RGB").save(full_image_path, "JPEG", quality=95)
        
        # Update registry
        registry.approve(character_name, state, str(face_crop_path), str(full_image_path))
        registry.save()
        
        return str(face_crop_path)

    def _face_crop(self, image: Image.Image) -> Image.Image:
        """
        Basic center-weighted crop with 20% padding.
        TODO: Upgrade to InsightFace when available.
        """
        width, height = image.size
        
        # Target a square crop around the upper-center area (where faces usually are)
        # For a 512x768 image, face is likely in top half.
        
        crop_size = min(width, height) // 2
        
        left = (width - crop_size) // 2
        top = height // 6  # Higher than center
        right = left + crop_size
        bottom = top + crop_size
        
        # Ensure we stay within bounds
        left = max(0, left)
        top = max(0, top)
        right = min(width, right)
        bottom = min(height, bottom)
        
        return image.crop((left, top, right, bottom))
