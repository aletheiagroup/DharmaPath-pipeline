"""
tests/test_assembler.py

Tests for ChapterAssembler — verifies panel resizing and stacking logic.
"""

import pytest
from pathlib import Path
from PIL import Image
from dharmapath.assembler.assembler import ChapterAssembler
from dharmapath.models.screenplay import Screenplay, Chapter, Panel, Size, Beat, ShotType, Arc, Path as LearningPath

@pytest.fixture
def assembler():
    return ChapterAssembler()

@pytest.fixture
def temp_image_dir(tmp_path):
    d = tmp_path / "panels"
    d.mkdir()
    return d

def create_mock_image(path, width, height, color=(255, 0, 0)):
    img = Image.new("RGB", (width, height), color)
    img.save(path, "PNG")

def test_assemble_full_panels(assembler, temp_image_dir):
    chapter = Chapter(chapter_id="itihaasa_ch01", path=LearningPath.itihaasa, arc=Arc.divine, title="T", description="D", arc_number=1)
    panels = [
        Panel(panel_id="p01", size=Size.full, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M"),
        Panel(panel_id="p02", size=Size.full, beat=Beat.escalation, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M")
    ]
    screenplay = Screenplay(chapter=chapter, panels=panels)
    
    # Create mock images (1000x500 each)
    create_mock_image(temp_image_dir / "dp_ch01_p01_v1.png", 1000, 500)
    create_mock_image(temp_image_dir / "dp_ch01_p02_v1.png", 1000, 500)
    
    output_path = assembler.assemble_chapter(screenplay, temp_image_dir)
    
    assert output_path.exists()
    img = Image.open(output_path)
    # Each 1000x500 becomes 800x400
    assert img.width == 800
    assert img.height == 800 # 400 + 400

def test_assemble_half_panels(assembler, temp_image_dir):
    chapter = Chapter(chapter_id="itihaasa_ch01", path=LearningPath.itihaasa, arc=Arc.divine, title="T", description="D", arc_number=1)
    panels = [
        Panel(panel_id="p01", size=Size.half, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M"),
        Panel(panel_id="p02", size=Size.half, beat=Beat.escalation, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M")
    ]
    screenplay = Screenplay(chapter=chapter, panels=panels)
    
    create_mock_image(temp_image_dir / "dp_ch01_p01_v1.png", 500, 500)
    create_mock_image(temp_image_dir / "dp_ch01_p02_v1.png", 500, 500)
    
    output_path = assembler.assemble_chapter(screenplay, temp_image_dir)
    
    assert output_path.exists()
    img = Image.open(output_path)
    # Both halves on one row (800 wide, 400 high)
    assert img.width == 800
    assert img.height == 400

def test_assemble_quarter_panels(assembler, temp_image_dir):
    chapter = Chapter(chapter_id="itihaasa_ch01", path=LearningPath.itihaasa, arc=Arc.divine, title="T", description="D", arc_number=1)
    panels = [
        Panel(panel_id="p01", size=Size.quarter, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M"),
        Panel(panel_id="p02", size=Size.quarter, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M"),
        Panel(panel_id="p03", size=Size.quarter, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M"),
        Panel(panel_id="p04", size=Size.quarter, beat=Beat.hook, shot_type=ShotType.medium, action="A", environment="E", lighting="L", camera="C", mood="M")
    ]
    screenplay = Screenplay(chapter=chapter, panels=panels)
    
    for i in range(1, 5):
        create_mock_image(temp_image_dir / f"dp_ch01_p0{i}_v1.png", 400, 400)
        
    output_path = assembler.assemble_chapter(screenplay, temp_image_dir)
    
    assert output_path.exists()
    img = Image.open(output_path)
    # 4 quarters on one row (800 wide, 200 high)
    assert img.width == 800
    assert img.height == 200
