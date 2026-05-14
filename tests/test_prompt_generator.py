"""
tests/test_prompt_generator.py

Tests for PromptGenerator — ensures that prompts are correctly assembled
from screenplay panels, palettes, and style profiles.
"""

import pytest
from pathlib import Path
from dharmapath.prompt_generator.generator import PromptGenerator
from dharmapath.models.screenplay import Screenplay, Panel, Chapter, Size, Beat, ShotType, Arc, Path as LearningPath
from dharmapath.registry.registry import CharacterRegistry

@pytest.fixture
def generator():
    return PromptGenerator()

@pytest.fixture
def registry():
    reg = CharacterRegistry()
    reg.load()
    # Register a test character
    reg.register("Arjuna", "A warrior prince with a bow", "itihaasa_ch01")
    reg.approve("Arjuna", "default", "data/characters/arjuna_default.jpg", "data/characters_full/arjuna_default.jpg")
    return reg

@pytest.fixture
def sample_chapter():
    return Chapter(
        chapter_id="itihaasa_ch01",
        path=LearningPath.itihaasa,
        arc=Arc.conflict,
        title="The Beginning",
        description="A test chapter",
        arc_number=1
    )

@pytest.fixture
def sample_panel():
    return Panel(
        panel_id="p01",
        size=Size.full,
        beat=Beat.hook,
        shot_type=ShotType.establishing,
        characters=["Arjuna"],
        action="Arjuna stands on the battlefield",
        environment="Kurukshetra",
        lighting="Golden hour",
        camera="Wide shot",
        mood="Tense",
        dialogue=[]
    )

def test_generate_panel_prompt(generator, sample_panel, sample_chapter, registry):
    prompt = generator.generate_panel_prompt(sample_panel, sample_chapter, registry)
    
    assert "positive" in prompt
    assert "negative" in prompt
    assert isinstance(prompt["positive"], str)
    assert isinstance(prompt["negative"], str)
    
    # Check if character description is included
    assert "A warrior prince with a bow" in prompt["positive"]
    # Check if action is included
    assert "Arjuna stands on the battlefield" in prompt["positive"]
    # Check if style tags are included (from itihaasa profile)
    assert "manhwa style" in prompt["positive"]
    # Check if palette tags are included (from conflict palette)
    assert "dramatic lighting" in prompt["positive"]

def test_generate_batch(generator, sample_chapter, registry):
    panels = [
        Panel(panel_id="p01", size=Size.full, beat=Beat.hook, shot_type=ShotType.establishing, action="Action 1", environment="Env 1", lighting="Light 1", camera="Cam 1", mood="Mood 1"),
        Panel(panel_id="p02", size=Size.half, beat=Beat.escalation, shot_type=ShotType.medium, action="Action 2", environment="Env 2", lighting="Light 2", camera="Cam 2", mood="Mood 2")
    ]
    screenplay = Screenplay(chapter=sample_chapter, panels=panels)
    
    prompts = generator.generate_batch(screenplay, registry)
    
    assert len(prompts) == 2
    assert "Action 1" in prompts[0]["positive"]
    assert "Action 2" in prompts[1]["positive"]

def test_generate_character_prompt(generator):
    prompt = generator.generate_character_prompt("Arjuna", "A warrior prince", 0)
    
    assert "positive" in prompt
    assert "A warrior prince" in prompt["positive"]
    assert "character portrait" in prompt["positive"]
    assert "frontal view" in prompt["positive"]
