#!/usr/bin/env bash
# ============================================================
# DharmaPath Pipeline — WSL Setup Script
# Run once inside WSL to set up the Python environment.
# Usage: bash scripts/setup_wsl.sh
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🕉️  DharmaPath Pipeline — WSL Setup${NC}"
echo "============================================"

# ── Check Python version ──────────────────────────────────────
echo -e "\n${YELLOW}[1/5] Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_MAJOR=3
REQUIRED_MINOR=11

MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt "$REQUIRED_MAJOR" ] || ([ "$MAJOR" -eq "$REQUIRED_MAJOR" ] && [ "$MINOR" -lt "$REQUIRED_MINOR" ]); then
    echo -e "${RED}❌ Python $PYTHON_VERSION found. Python 3.11+ required.${NC}"
    echo "Install with: sudo apt install python3.11 python3.11-venv"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"

# ── Create virtual environment ────────────────────────────────
echo -e "\n${YELLOW}[2/5] Creating virtual environment...${NC}"
if [ -d ".venv" ]; then
    echo "  .venv already exists — skipping creation."
else
    python3 -m venv .venv
    echo -e "${GREEN}✓ .venv created${NC}"
fi

# ── Activate venv ─────────────────────────────────────────────
echo -e "\n${YELLOW}[3/5] Activating virtual environment...${NC}"
source .venv/bin/activate
echo -e "${GREEN}✓ Activated: $(which python)${NC}"

# ── Install dependencies ──────────────────────────────────────
echo -e "\n${YELLOW}[4/5] Installing dependencies from requirements.txt...${NC}"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo -e "${GREEN}✓ Dependencies installed${NC}"

# ── Copy .env ─────────────────────────────────────────────────
echo -e "\n${YELLOW}[5/5] Environment variables...${NC}"
if [ -f ".env" ]; then
    echo "  .env already exists — skipping copy."
else
    cp .env.example .env
    echo -e "${GREEN}✓ .env created from .env.example${NC}"
    echo -e "${YELLOW}  ⚠ Fill in your real values in .env before running the pipeline.${NC}"
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Fill in .env with your RunPod URL and R2 credentials"
echo "  2. Run: bash scripts/start_web.sh"
echo "  3. Or use CLI: source .venv/bin/activate && dharmapath --help"
