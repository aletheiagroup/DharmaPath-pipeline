#!/usr/bin/env bash
# ============================================================
# DharmaPath Pipeline — Start FastAPI dev server
# Usage: bash scripts/start_web.sh
# ============================================================

set -euo pipefail

# Activate venv if not already active
if [ -z "${VIRTUAL_ENV:-}" ]; then
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    else
        echo "❌ No .venv found. Run: bash scripts/setup_wsl.sh first."
        exit 1
    fi
fi

# Load port from .env if available
WEB_PORT=${WEB_PORT:-8000}
WEB_HOST=${WEB_HOST:-0.0.0.0}

echo "🕉️  Starting DharmaPath Pipeline web UI..."
echo "   http://localhost:${WEB_PORT}"
echo ""

uvicorn web.app:app \
    --host "$WEB_HOST" \
    --port "$WEB_PORT" \
    --reload \
    --reload-dir dharmapath \
    --reload-dir web \
    --log-level info
