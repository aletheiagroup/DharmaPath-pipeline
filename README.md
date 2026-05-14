# 🕉️ dharmapath-pipeline

> Manhwa generation pipeline for DharmaPath — takes structured screenplay JSON and produces Webtoon-ready vertical panel strips.

---

## Overview

This pipeline is **semi-automated**:
- **Human decisions:** screenplay writing, character selection, impact panel review
- **Automated:** prompt generation, ComfyUI image generation (via RunPod API), panel assembly, R2 upload

All heavy compute (image generation, ESRGAN upscale, colour grading) happens on **RunPod** via the ComfyUI REST API. No local GPU required.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Web UI | FastAPI + Jinja2 (no React, no build step) |
| Image processing | Pillow |
| ComfyUI client | httpx (async) |
| Storage | Cloudflare R2 via boto3 (S3-compatible) |
| Config | pydantic-settings + python-dotenv |
| Data models | Pydantic v2 |
| CLI | Typer |
| Tests | pytest |

---

## Prerequisites

- **WSL2** (Ubuntu 22.04+) on Windows
- **Python 3.11+** inside WSL
- A **RunPod** instance running ComfyUI (with DWPose, ESRGAN, IP-Adapter nodes installed)
- A **Cloudflare R2** bucket named `dharmapath`

---

## WSL Setup

Run the automated setup script inside WSL:

```bash
bash scripts/setup_wsl.sh
```

This will:
1. Check Python 3.11+ is available
2. Create a `.venv` virtual environment
3. Install all pinned dependencies from `requirements.txt`
4. Copy `.env.example` → `.env` (you fill in real values)

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
```

| Variable | Where to find it |
|----------|-----------------|
| `COMFYUI_BASE_URL` | RunPod Dashboard → your pod → Connect → HTTP Service port 8188 |
| `RUNPOD_API_KEY` | RunPod Dashboard → Settings → API Keys |
| `R2_ACCOUNT_ID` | Cloudflare Dashboard → R2 → Overview |
| `R2_ACCESS_KEY_ID` | Cloudflare Dashboard → R2 → Manage R2 API Tokens → Create Token |
| `R2_SECRET_ACCESS_KEY` | Same token creation page (shown once) |
| `R2_BUCKET_NAME` | The bucket you created — default: `dharmapath` |

---

## Running the Web UI

```bash
bash scripts/start_web.sh
```

Then open `http://localhost:8000` in your Windows browser.

---

## CLI Usage

```bash
# Activate venv first
source .venv/bin/activate

# Validate a screenplay JSON
dharmapath validate data/screenplays/my_chapter.json

# Run full pipeline (validate → generate → assemble → upload)
dharmapath generate data/screenplays/my_chapter.json

# Assemble panels into strip (if panels already generated)
dharmapath assemble itihaasa_ch01

# Split assembled strip into Webtoon episode files
dharmapath export itihaasa_ch01

# Check RunPod ComfyUI is reachable
dharmapath check-runpod
```

---

## Full Pipeline Workflow

```
Screenplay JSON
    ↓ dharmapath validate
CLI catches hard rule violations
    ↓ Web UI: character candidate generation (ComfyUI → 9 face variations)
    ↓ Click to approve → Pillow face-crops → saved to registry
    ↓ dharmapath generate
Prompt Generator builds ComfyUI prompts (Jinja2 + palette configs)
    ↓
Workflow Builder injects prompts + LoRA weights + ControlNet + IP-Adapter
    ↓
ComfyUI on RunPod generates panels → ESRGAN upscale → colour grading
    ↓ every 5 panels: boto3 auto-uploads to Cloudflare R2
DWPose node flags anatomically anomalous panels
    ↓
FastAPI review page — human marks additional panels for correction
    ↓ flagged panels → ComfyUI inpainting workflow
Pillow assembler stacks panels into vertical strip (800px wide)
    ↓
Pillow exporter splits at 5120px → episode JPGs at 90% quality
    ↓
Final episodes upload to R2 → ready for Webtoon upload
```

---

## Repo Structure

```
dharmapath-pipeline/
├── config/           ← Settings, palettes, style profiles
├── data/             ← Screenplays, generated assets, character refs
├── dharmapath/       ← Core Python package
│   ├── models/       ← Pydantic data models
│   ├── registry/     ← Character registry
│   ├── validator/    ← Screenplay validation rules
│   ├── prompt_generator/  ← Panel prompt construction
│   ├── comfyui/      ← ComfyUI API client + workflow builder
│   ├── character_designer/ ← Candidate generation + face crop
│   ├── assembler/    ← Panel strip assembly + export
│   ← storage/       ← Cloudflare R2 client
│   └── pipeline/     ← End-to-end chapter runner
├── web/              ← FastAPI app + Jinja2 templates
├── cli/              ← Typer CLI entry point
├── scripts/          ← WSL setup + utility scripts
└── tests/            ← pytest tests + fixtures
```

---

## Testing

```bash
pytest                  # run all tests
pytest tests/test_validator.py -v    # validator rules only
pytest tests/test_assembler.py -v    # assembler logic only
```

---

© 2026 DharmaPath — Vishwajeet Patil & Kedar Mujumdar
