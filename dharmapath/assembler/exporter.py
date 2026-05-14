"""
dharmapath/assembler/exporter.py

ChapterExporter — splits the assembled strip into Webtoon episodes.
"""

import logging
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

class ChapterExporter:
    """
    Handles splitting long vertical strips into individual files.
    Webtoon limit: 5120px height.
    Output: 800px wide, JPG 90%.
    """
    
    MAX_HEIGHT = 5120
    WIDTH = 800
    QUALITY = 90

    def split_episodes(self, strip_path: Path, chapter_id: str) -> list[Path]:
        """
        Splits the strip and returns a list of episode file paths.
        """
        img = Image.open(strip_path)
        width, height = img.size
        
        episodes = []
        num_episodes = (height // self.MAX_HEIGHT) + (1 if height % self.MAX_HEIGHT > 0 else 0)
        
        output_dir = strip_path.parent / "episodes"
        output_dir.mkdir(parents=True, exist_ok=True)

        for i in range(num_episodes):
            top = i * self.MAX_HEIGHT
            bottom = min((i + 1) * self.MAX_HEIGHT, height)
            
            # Crop episode
            episode_img = img.crop((0, top, width, bottom))
            
            # Save as JPG
            episode_filename = f"dp_{chapter_id}_ep{i+1:02d}.jpg"
            episode_path = output_dir / episode_filename
            
            # Convert to RGB (in case strip is RGBA)
            if episode_img.mode in ("RGBA", "P"):
                episode_img = episode_img.convert("RGB")
                
            episode_img.save(episode_path, "JPEG", quality=self.QUALITY)
            episodes.append(episode_path)
            
            logger.info(f"Exported episode {i+1}: {episode_path}")
            
        return episodes
