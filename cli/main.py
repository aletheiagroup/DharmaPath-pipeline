"""
cli/main.py

Typer CLI for the DharmaPath Pipeline.
"""

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from dharmapath.pipeline.runner import ChapterRunner
from dharmapath.registry.registry import CharacterRegistry
from dharmapath.validator.screenplay_validator import ScreenplayValidator
from dharmapath.comfyui.client import ComfyUIClient
from dharmapath.prompt_generator.generator import PromptGenerator
from dharmapath.models.screenplay import Screenplay

# Configure logging to use Rich
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

app = typer.Typer(
    help="DharmaPath Manhwa Generation Pipeline CLI",
    add_completion=False
)
console = Console()

@app.command()
def validate(
    screenplay_path: Annotated[Path, typer.Argument(help="Path to screenplay JSON file")],
    registry_path: Annotated[str, typer.Option(help="Path to character registry")] = "dharmapath/registry/characters.json",
):
    """
    Validate a screenplay against all 16 rules.
    Checks for structural, narrative, and character approval errors.
    """
    if not screenplay_path.exists():
        console.print(f"[red]Error:[/red] File {screenplay_path} not found.")
        raise typer.Exit(1)

    registry = CharacterRegistry(Path(registry_path))
    registry.load()
    validator = ScreenplayValidator(registry)
    
    try:
        screenplay = Screenplay.model_validate_json(screenplay_path.read_text(encoding="utf-8"))
        result = validator.validate(screenplay)
        
        console.print(f"\n[bold]Validation Results for {screenplay.chapter.chapter_id}:[/bold]")
        console.print(result.summary())
        
        if result.errors:
            table = Table(title="Rule Violations")
            table.add_column("Panel", style="cyan")
            table.add_column("Rule", style="magenta")
            table.add_column("Severity", style="bold")
            table.add_column("Message")
            
            for err in result.errors:
                severity_style = "red" if err.severity == "error" else "yellow"
                table.add_row(
                    err.panel_id,
                    err.rule_name,
                    f"[{severity_style}]{err.severity.upper()}[/{severity_style}]",
                    err.message
                )
            console.print(table)
            
        if not result.passed():
            console.print("\n[red]Please fix hard errors before attempting generation.[/red]")
            raise typer.Exit(1)
        else:
            console.print("\n[green]Ready for generation![/green]")
            
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def generate(
    screenplay_path: Annotated[Path, typer.Argument(help="Path to screenplay JSON file")],
    upload: Annotated[bool, typer.Option("--upload/--no-upload", help="Upload final episodes to R2")] = True,
    registry_path: Annotated[str, typer.Option(help="Path to character registry")] = "dharmapath/registry/characters.json",
):
    """
    Run the full generation pipeline.
    Validates, generates (ComfyUI), assembles, and exports.
    """
    runner = ChapterRunner(registry_path=registry_path)
    
    console.print("[bold blue]DharmaPath Pipeline Runner[/bold blue]")
    console.print(f"Screenplay: {screenplay_path}")
    console.print(f"Registry:   {registry_path}")
    console.print(f"Upload:     {'Enabled' if upload else 'Disabled'}")
    console.print("-" * 40)

    # Note: We use asyncio.run because ChapterRunner is async
    try:
        result = asyncio.run(runner.run(screenplay_path, upload=upload))
    except Exception as e:
        console.print(f"\n[bold red]Runner Crashed![/bold red] {e}")
        raise typer.Exit(1)
    
    if result.success:
        console.print(f"\n[bold green]Success![/bold green] Chapter {result.chapter_id} generated.")
        console.print(f"Duration: {result.duration_seconds:.1f}s")
        console.print(f"Panels:   {result.panels_generated}")
        
        if result.episode_paths:
            console.print("\n[bold]Exported Episodes:[/bold]")
            for p in result.episode_paths:
                console.print(f"  - {p}")
                
        if result.r2_urls:
            console.print("\n[bold]R2 URLs:[/bold]")
            for url in result.r2_urls:
                console.print(f"  - {url}")
                
        if result.panels_flagged_human:
            console.print(f"\n[yellow]Human Review Required:[/yellow] {len(result.panels_flagged_human)} panels flagged: {', '.join(result.panels_flagged_human)}")
    else:
        console.print(f"\n[bold red]Pipeline Failed![/bold red]")
        for err in result.errors:
            console.print(f"  - {err}")
        raise typer.Exit(1)

@app.command()
def check_runpod():
    """
    Verify that the RunPod ComfyUI instance is reachable.
    Checks endpoint and API key configuration.
    """
    async def _check():
        async with ComfyUIClient() as client:
            return await client.health_check()
            
    with console.status("[bold green]Checking RunPod health..."):
        try:
            ok = asyncio.run(_check())
        except Exception as e:
            console.print(f"[red]Error connecting to ComfyUI:[/red] {e}")
            raise typer.Exit(1)

    if ok:
        console.print("[bold green]RunPod ComfyUI is ONLINE and reachable.[/bold green]")
    else:
        console.print("[bold red]RunPod ComfyUI is OFFLINE or unreachable.[/bold red]")
        console.print("Check your COMFYUI_BASE_URL and RUNPOD_API_KEY in .env")
        raise typer.Exit(1)

@app.command()
def generate_prompts(
    screenplay_path: Annotated[Path, typer.Argument(help="Path to screenplay JSON file")],
    registry_path: Annotated[str, typer.Option(help="Path to character registry")] = "dharmapath/registry/characters.json",
):
    """
    Generate positive and negative prompts for all panels in the screenplay.
    """
    if not screenplay_path.exists():
        console.print(f"[red]Error:[/red] File {screenplay_path} not found.")
        raise typer.Exit(1)

    registry = CharacterRegistry(Path(registry_path))
    registry.load()
    prompt_gen = PromptGenerator()

    try:
        screenplay = Screenplay.model_validate_json(screenplay_path.read_text(encoding="utf-8"))
        prompts = prompt_gen.generate_batch(screenplay, registry)
        
        for panel, prompt in zip(screenplay.panels, prompts):
            console.print(f"\n[bold green]Panel: {panel.panel_id}[/bold green]")
            console.print(f"[bold]Positive Prompt:[/bold]\n{prompt['positive']}")
            console.print(f"[bold]Negative Prompt:[/bold]\n{prompt['negative']}")
            if prompt.get("controlnet_image"):
                console.print(f"[bold]ControlNet Pose Image:[/bold]\n{prompt['controlnet_image']}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def build_workflow(
    screenplay_path: Annotated[Path, typer.Argument(help="Path to screenplay JSON file")],
    panel_id: Annotated[str, typer.Argument(help="The panel ID to build the workflow for (e.g. p01)")],
    output_path: Annotated[Path, typer.Option(help="Where to save the workflow JSON")] = Path("workflow_output.json"),
    registry_path: Annotated[str, typer.Option(help="Path to character registry")] = "dharmapath/registry/characters.json",
):
    """
    Injects panel parameters and builds a ComfyUI workflow JSON for a single panel.
    """
    if not screenplay_path.exists():
        console.print(f"[red]Error:[/red] File {screenplay_path} not found.")
        raise typer.Exit(1)

    from dharmapath.comfyui.workflow_builder import WorkflowBuilder
    import json

    registry = CharacterRegistry(Path(registry_path))
    registry.load()
    prompt_gen = PromptGenerator()
    workflow_builder = WorkflowBuilder()

    # Load style profiles for workflow building
    style_profiles = json.loads(
        (Path("config") / "style_profiles.json").read_text(encoding="utf-8")
    )

    try:
        screenplay = Screenplay.model_validate_json(screenplay_path.read_text(encoding="utf-8"))
        
        # Find the specific panel
        panel = next((p for p in screenplay.panels if p.panel_id == panel_id), None)
        if not panel:
            console.print(f"[red]Error:[/red] Panel {panel_id} not found in screenplay.")
            raise typer.Exit(1)

        prompt_data = prompt_gen.generate_panel_prompt(panel, screenplay.chapter, registry)
        
        # Resolve character face references
        path_key = screenplay.chapter.path.value
        style_profile = style_profiles.get(path_key, style_profiles.get("itihaasa", {}))
        
        character_face_paths = {}
        character_ip_weights = {}
        for char_name in panel.characters:
            state = registry.get_state_for_arc(char_name, screenplay.chapter.arc_number)
            if state and state.reference_image:
                character_face_paths[char_name] = state.reference_image
                character_ip_weights[char_name] = style_profile.get("ip_adapter_weight", 0.65)

        workflow = workflow_builder.build_panel_workflow(
            prompt=prompt_data,
            panel=panel,
            chapter=screenplay.chapter,
            style_profile=style_profile,
            character_face_paths=character_face_paths,
            character_ip_weights=character_ip_weights
        )

        output_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
        console.print(f"[green]Workflow JSON successfully written to {output_path}[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def generate_panel(
    workflow_path: Annotated[Path, typer.Argument(help="Path to the built workflow JSON file")],
    output_path: Annotated[Path, typer.Argument(help="Path to save the generated panel PNG image")],
):
    """
    Generate a panel image by sending a built workflow JSON directly to ComfyUI.
    """
    if not workflow_path.exists():
        console.print(f"[red]Error:[/red] Workflow file {workflow_path} not found.")
        raise typer.Exit(1)

    import json

    async def _generate():
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        async with ComfyUIClient() as client:
            console.print(f"[bold blue]Sending workflow to ComfyUI and polling...[/bold blue]")
            await client.generate_panel(workflow, str(output_path))

    try:
        asyncio.run(_generate())
        console.print(f"[green]Panel successfully generated and saved to {output_path}[/green]")
    except Exception as e:
        console.print(f"[red]Error during ComfyUI generation:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def assemble(
    screenplay_path: Annotated[Path, typer.Argument(help="Path to screenplay JSON file")],
    panel_dir: Annotated[Path, typer.Argument(help="Directory containing the generated panel PNG images")],
    output_path: Annotated[Optional[Path], typer.Option(help="Path to save the assembled vertical strip PNG")] = None,
):
    """
    Assemble generated panel images into a single vertical strip.
    """
    if not screenplay_path.exists():
        console.print(f"[red]Error:[/red] Screenplay file {screenplay_path} not found.")
        raise typer.Exit(1)
    if not panel_dir.exists():
        console.print(f"[red]Error:[/red] Panel directory {panel_dir} not found.")
        raise typer.Exit(1)

    from dharmapath.assembler.assembler import ChapterAssembler

    try:
        screenplay = Screenplay.model_validate_json(screenplay_path.read_text(encoding="utf-8"))
        assembler = ChapterAssembler()
        
        console.print(f"[bold blue]Assembling panels from {panel_dir}...[/bold blue]")
        strip_path = assembler.assemble_chapter(screenplay, panel_dir)
        
        if output_path:
            shutil_path = Path(strip_path)
            if shutil_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy(shutil_path, output_path)
                console.print(f"[green]Assembled strip saved to {output_path}[/green]")
            else:
                console.print(f"[red]Error: Assembled strip not found at expected path {strip_path}[/red]")
        else:
            console.print(f"[green]Assembled strip successfully created at {strip_path}[/green]")
    except Exception as e:
        console.print(f"[red]Error during assembly:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def export_episodes(
    strip_path: Annotated[Path, typer.Argument(help="Path to the assembled vertical strip PNG file")],
    chapter_id: Annotated[str, typer.Argument(help="Chapter ID (e.g. itihaasa_ch01)")],
):
    """
    Slice the assembled vertical strip into Webtoon-compliant episodes (800px wide, max 5120px height JPGs).
    """
    if not strip_path.exists():
        console.print(f"[red]Error:[/red] Strip file {strip_path} not found.")
        raise typer.Exit(1)

    from dharmapath.assembler.exporter import ChapterExporter

    try:
        exporter = ChapterExporter()
        console.print(f"[bold blue]Slicing vertical strip {strip_path}...[/bold blue]")
        episode_paths = exporter.split_episodes(strip_path, chapter_id)
        
        console.print("\n[bold green]Export completed successfully![/bold green]")
        for ep in episode_paths:
            console.print(f"  - {ep}")
    except Exception as e:
        console.print(f"[red]Error during export:[/red] {e}")
        raise typer.Exit(1)

@app.command()
def upload_episodes(
    chapter_id: Annotated[str, typer.Argument(help="Chapter ID")],
    episode_files: Annotated[list[Path], typer.Argument(help="List of episode JPG files to upload")],
):
    """
    Upload Webtoon-ready episode JPG files to Cloudflare R2 storage.
    """
    from dharmapath.storage.r2_client import R2Client

    try:
        r2 = R2Client()
        console.print(f"[bold blue]Uploading {len(episode_files)} episodes to R2...[/bold blue]")
        r2_urls = []
        for ep_path in episode_files:
            if not ep_path.exists():
                console.print(f"[yellow]Warning: File {ep_path} not found. Skipping.[/yellow]")
                continue
            url = r2.upload_episode(ep_path, chapter_id)
            r2_urls.append((ep_path.name, url))
        
        console.print("\n[bold green]Upload completed successfully![/bold green]")
        for name, url in r2_urls:
            console.print(f"  - {name}: {url}")
    except Exception as e:
        console.print(f"[red]Error during R2 upload:[/red] {e}")
        raise typer.Exit(1)

if __name__ == "__main__":
    app()
