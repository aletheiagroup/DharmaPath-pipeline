"""
dharmapath/assembler/assembler.py

ChapterAssembler — handles vertical strip assembly from generated panels.
Handles resizing and quarter-panel row stacking.
"""

import logging
from pathlib import Path
from PIL import Image
from dharmapath.models.screenplay import Screenplay, Size

logger = logging.getLogger(__name__)

class ChapterAssembler:
    """
    Assembles individual panel images into a single vertical strip.
    Webtoon standard width: 800px.
    """
    
    CANVAS_WIDTH = 800
    FULL_WIDTH = 800
    HALF_WIDTH = 400
    QUARTER_WIDTH = 200

    def assemble_chapter(self, screenplay: Screenplay, panel_image_dir: Path) -> Path:
        """
        Reads screenplay panels, loads images, and assembles the strip.
        Returns path to the assembled PNG.
        """
        panels = screenplay.panels
        assembled_images = []
        
        i = 0
        while i < len(panels):
            panel = panels[i]
            img_path = panel_image_dir / f"dp_ch{screenplay.chapter.chapter_id.split('ch')[-1]}_p{panel.panel_id.lstrip('p')}_v1.png"
            
            if not img_path.exists():
                logger.error(f"Panel image not found: {img_path}")
                # For now, create a placeholder if missing
                img = Image.new("RGB", (self.FULL_WIDTH, 400), (200, 200, 200))
            else:
                img = Image.open(img_path)

            if panel.size == Size.full:
                # Resize to 800px wide, maintain aspect ratio
                w, h = img.size
                new_h = int(h * (self.FULL_WIDTH / w))
                img = img.resize((self.FULL_WIDTH, new_h), Image.Resampling.LANCZOS)
                assembled_images.append(img)
                i += 1
            
            elif panel.size == Size.half:
                # Resize to 400px wide
                w, h = img.size
                new_h = int(h * (self.HALF_WIDTH / w))
                img = img.resize((self.HALF_WIDTH, new_h), Image.Resampling.LANCZOS)
                
                # Check if next panel is also half
                if i + 1 < len(panels) and panels[i+1].size == Size.half:
                    next_panel = panels[i+1]
                    next_img_path = panel_image_dir / f"dp_ch{screenplay.chapter.chapter_id.split('ch')[-1]}_p{next_panel.panel_id.lstrip('p')}_v1.png"
                    if next_img_path.exists():
                        next_img = Image.open(next_img_path)
                    else:
                        next_img = Image.new("RGB", (self.HALF_WIDTH, img.height), (180, 180, 180))
                    
                    # Resize next image to match current height
                    nw, nh = next_img.size
                    next_h = int(nh * (self.HALF_WIDTH / nw))
                    next_img = next_img.resize((self.HALF_WIDTH, next_h), Image.Resampling.LANCZOS)
                    
                    # Target height for both is the max of the two
                    target_h = max(img.height, next_img.height)
                    
                    # Create row
                    row = Image.new("RGB", (self.FULL_WIDTH, target_h), (255, 255, 255))
                    row.paste(img, (0, 0))
                    row.paste(next_img, (self.HALF_WIDTH, 0))
                    assembled_images.append(row)
                    i += 2
                else:
                    # Single half panel — center it
                    row = Image.new("RGB", (self.FULL_WIDTH, img.height), (255, 255, 255))
                    row.paste(img, (self.HALF_WIDTH // 2, 0))
                    assembled_images.append(row)
                    i += 1
            
            elif panel.size == Size.quarter:
                # Groups of 4
                row_panels = []
                for j in range(4):
                    if i + j < len(panels) and panels[i+j].size == Size.quarter:
                        p = panels[i+j]
                        p_path = panel_image_dir / f"dp_ch{screenplay.chapter.chapter_id.split('ch')[-1]}_p{p.panel_id.lstrip('p')}_v1.png"
                        if p_path.exists():
                            p_img = Image.open(p_path)
                        else:
                            p_img = Image.new("RGB", (self.QUARTER_WIDTH, 200), (150, 150, 150))
                        
                        pw, ph = p_img.size
                        p_h = int(ph * (self.QUARTER_WIDTH / pw))
                        p_img = p_img.resize((self.QUARTER_WIDTH, p_h), Image.Resampling.LANCZOS)
                        row_panels.append(p_img)
                    else:
                        break
                
                if row_panels:
                    target_h = max(p.height for p in row_panels)
                    row = Image.new("RGB", (self.FULL_WIDTH, target_h), (255, 255, 255))
                    for idx, p_img in enumerate(row_panels):
                        row.paste(p_img, (idx * self.QUARTER_WIDTH, 0))
                    assembled_images.append(row)
                    i += len(row_panels)
                else:
                    i += 1
            else:
                i += 1

        # Calculate total height
        total_height = sum(img.height for img in assembled_images)
        final_strip = Image.new("RGB", (self.FULL_WIDTH, total_height), (255, 255, 255))
        
        y_offset = 0
        for img in assembled_images:
            final_strip.paste(img, (0, y_offset))
            y_offset += img.height
            
        output_path = panel_image_dir.parent / f"{screenplay.chapter.chapter_id}_assembled.png"
        final_strip.save(output_path, "PNG")
        
        logger.info(f"Chapter assembled: {output_path} ({total_height}px height)")
        return output_path
