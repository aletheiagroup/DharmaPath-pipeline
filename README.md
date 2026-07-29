# 🕉️ dharmapath-pipeline

> Manhwa generation pipeline for DharmaPath — takes structured screenplay JSON and produces Webtoon-ready vertical panel strips.

---

## Overview

This pipeline is **semi-automated**:
- **Human decisions:** screenplay writing, character selection, impact panel review
- **Automated:** prompt generation, ComfyUI image generation (via GCE GPU), panel assembly, cloud upload

All heavy compute (image generation, ESRGAN upscale, colour grading) runs on a **GCE VM with T4 GPU** via the ComfyUI REST API. No local GPU required.

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Backend API | FastAPI (async REST API) |
| Frontend | **dharmapath-studio** — React 19 + TypeScript + Vite (separate repo) |
| Image processing | Pillow |
| ComfyUI client | httpx (async) with retry + circuit breaker |
| AI / LLM | Google Gemini 2.0 Flash via `google-genai` |
| Storage | Cloudflare R2 (boto3) or Google Cloud Storage |
| Config | pydantic-settings + python-dotenv |
| Data models | Pydantic v2 |
| CLI | Typer + Rich |
| Tests | pytest + pytest-asyncio |

---

## Prerequisites

- **Python 3.11+** (Windows, WSL, or Linux)
- A **GCE VM** (e.g. `g2-standard-4` with L4, or any instance with T4 GPU) running ComfyUI via Docker
- A **Cloudflare R2** bucket and/or **Google Cloud Storage** bucket named `dharmapath`
- A **Google API key** for Gemini (get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey))

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/aletheiagroup/DharmaPath-pipeline.git
cd DharmaPath-pipeline
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values (see Environment Variables below)
```

### 3. Set up ComfyUI on GCE VM

SSH into your GPU VM and run the automated setup script:

```bash
sudo ./scripts/gcp_setup.sh
```

This installs Docker, NVIDIA Container Toolkit, downloads all required models (Illustrious XL, ESRGAN, ControlNet, IP-Adapter), and starts ComfyUI on port 8188.

> **Firewall:** Create a GCP firewall rule to allow TCP:8188 from your IP:
> ```bash
> gcloud compute firewall-rules create allow-comfyui \
>   --allow=tcp:8188 \
>   --source-ranges=<YOUR_IP>/32 \
>   --target-tags=comfyui-server
> ```

### 4. Verify connection

```bash
python scripts/check_runpod.py
```

### 5. Start the API server

```bash
bash scripts/start_web.sh
# or directly:
uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://localhost:8000` in your browser (or use **dharmapath-studio** as the frontend).

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the required values:

| Variable | Required | Where to find it |
|----------|----------|-----------------|
| `COMFYUI_BASE_URL` | ✅ | GCE VM external IP — printed after running `gcp_setup.sh` |
| `GOOGLE_API_KEY` | ✅ | [AI Studio](https://aistudio.google.com/apikey) — or leave empty if using ADC on GCE |
| `GEMINI_MODEL` | — | Default: `gemini-2.0-flash` |
| `GCP_PROJECT_ID` | — | GCP Console → Dashboard |
| `GCP_REGION` | — | Default: `asia-south1` |
| `GCS_BUCKET_NAME` | — | Your GCS bucket (default: `dharmapath`) |
| `R2_ACCOUNT_ID` | — | Cloudflare Dashboard → R2 → Overview |
| `R2_ACCESS_KEY_ID` | — | Cloudflare Dashboard → R2 → Manage R2 API Tokens |
| `R2_SECRET_ACCESS_KEY` | — | Same token creation page (shown once) |
| `R2_BUCKET_NAME` | — | Default: `dharmapath` |
| `RUNPOD_API_KEY` | — | Legacy — only if still using RunPod |

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

# Check ComfyUI is reachable
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
ComfyUI on GCE VM generates panels → ESRGAN upscale → colour grading
    ↓ every 5 panels: auto-uploads to cloud storage
DWPose node flags anatomically anomalous panels
    ↓
Review page — human marks additional panels for correction
    ↓ flagged panels → ComfyUI inpainting workflow
Pillow assembler stacks panels into vertical strip (800px wide)
    ↓
Pillow exporter splits at 5120px → episode JPGs at 90% quality
    ↓
Final episodes upload to cloud → ready for Webtoon upload
```

---

## Repo Structure

```
dharmapath-pipeline/
├── config/                  ← Settings, palettes, style profiles, model manifest
├── data/                    ← Screenplays, generated assets, character refs
├── dharmapath/              ← Core Python package
│   ├── models/              ← Pydantic data models (Screenplay, Character, Job)
│   ├── registry/            ← Character registry (characters.json, approval flow)
│   ├── validator/           ← Screenplay validation rules
│   ├── prompt_generator/    ← Panel prompt construction (Jinja2 templates)
│   ├── comfyui/             ← ComfyUI API client + workflow builder
│   ├── character_designer/  ← Candidate generation + face crop
│   ├── assembler/           ← Panel strip assembly + Webtoon export
│   ├── storage/             ← Cloudflare R2 + Google Cloud Storage clients
│   ├── genai/               ← Gemini 2.0 Flash client
│   ├── utils/               ← Retry with backoff, circuit breaker
│   └── pipeline/            ← End-to-end chapter runner
├── web/                     ← FastAPI REST API
│   ├── routes/              ← API endpoints (chapters, panels, generation, etc.)
│   ├── schemas/             ← Request/response Pydantic schemas
│   ├── services/            ← Business logic layer
│   └── store/               ← Job store, review store, task manager
├── cli/                     ← Typer CLI entry point
├── scripts/                 ← GCE setup, WSL setup, health check
└── tests/                   ← pytest tests + fixtures
```

---

## GCE VM Setup

The setup script (`scripts/gcp_setup.sh`) automates the full GPU server configuration:

1. Installs **Docker** + **Docker Compose**
2. Installs **NVIDIA Container Toolkit** for GPU passthrough
3. Creates persistent directories at `/opt/comfyui/`
4. Downloads models defined in `config/model_manifest.yaml`:
   - **Illustrious XL v0.1** (~6.5 GB) — base checkpoint
   - **RealESRGAN x4plus** (~67 MB) — upscaler
   - **ControlNet OpenPose SDXL** (~1.4 GB) — pose control
   - **IP-Adapter SDXL** (~700 MB) — character consistency
   - **CLIP Vision ViT-G** (~2.5 GB) — IP-Adapter dependency
5. Starts ComfyUI via `docker compose` on port 8188

A version-controlled Docker Compose file is also available at `docker-compose.gpu.yml`.

---

## Testing

```bash
pytest                               # run all tests
pytest tests/test_validator.py -v    # screenplay validation rules
pytest tests/test_assembler.py -v    # assembler logic
pytest tests/test_comfyui.py -v      # ComfyUI client (mocked)
pytest tests/test_retry.py -v        # retry + circuit breaker
pytest tests/test_runner.py -v       # pipeline runner
pytest tests/api/ -v                 # API endpoint tests
pytest -m live                       # live tests (requires running ComfyUI)
```

---

© 2026 DharmaPath — Vishwajeet Patil & Kedar Mujumdar
