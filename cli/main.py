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
from dharmapath.models.screenplay import Screenplay
from dharmapath.comfyui.client import ComfyUIClient

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

if __name__ == "__main__":
    app()
