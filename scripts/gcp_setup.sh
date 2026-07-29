#!/usr/bin/env bash
# =============================================================================
# scripts/gcp_setup.sh
#
# DharmaPath — GCE VM Setup Script
# Run this on a fresh GCE g2-standard-4 (L4 GPU) VM with Ubuntu 22.04.
#
# Usage:
#   chmod +x scripts/gcp_setup.sh
#   sudo ./scripts/gcp_setup.sh
#
# This script:
#   1. Installs Docker + Docker Compose
#   2. Installs NVIDIA Container Toolkit (GPU passthrough to Docker)
#   3. Creates persistent directories for ComfyUI models/outputs
#   4. Downloads required model files (Illustrious XL, ESRGAN, ControlNet)
#   5. Starts ComfyUI via Docker Compose
# =============================================================================

set -euo pipefail

# ── Colours for output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[⚠]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${BLUE}[→]${NC} $1"; }

# ── Check root ────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "Please run as root: sudo ./scripts/gcp_setup.sh"
    exit 1
fi

COMFYUI_HOME="/opt/comfyui"
MODELS_DIR="${COMFYUI_HOME}/models"
OUTPUT_DIR="${COMFYUI_HOME}/output"
CUSTOM_NODES_DIR="${COMFYUI_HOME}/custom_nodes"
INPUT_DIR="${COMFYUI_HOME}/input"

echo ""
echo "================================================"
echo "  DharmaPath — GCE ComfyUI Server Setup"
echo "================================================"
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# STEP 1: System Updates + Docker
# ═════════════════════════════════════════════════════════════════════════════

info "Step 1/5: Installing Docker..."

apt-get update -qq
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    python3-yaml

# Docker official GPG key + repo
if ! command -v docker &> /dev/null; then
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian bookworm stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    log "Docker installed."
else
    log "Docker already installed."
fi

# Add the current (non-root) user to docker group
REAL_USER="${SUDO_USER:-$USER}"
if [ "$REAL_USER" != "root" ]; then
    usermod -aG docker "$REAL_USER" 2>/dev/null || true
    log "Added $REAL_USER to docker group."
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 2: NVIDIA Container Toolkit
# ═════════════════════════════════════════════════════════════════════════════

info "Step 2/5: Installing NVIDIA Container Toolkit..."

if ! dpkg -l | grep -q nvidia-container-toolkit; then
    # Add NVIDIA GPG key + repo
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
    log "NVIDIA Container Toolkit installed and configured."
else
    log "NVIDIA Container Toolkit already installed."
fi

# Verify GPU is visible
if nvidia-smi &> /dev/null; then
    log "GPU detected:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1
else
    warn "nvidia-smi not working. GPU drivers may not be installed."
    warn "On GCE g2 instances, drivers should be pre-installed. If not, run:"
    warn "  sudo apt-get install -y nvidia-driver-535"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 3: Create Persistent Directories
# ═════════════════════════════════════════════════════════════════════════════

info "Step 3/5: Creating persistent directories at ${COMFYUI_HOME}..."

mkdir -p "${MODELS_DIR}/checkpoints"
mkdir -p "${MODELS_DIR}/loras"
mkdir -p "${MODELS_DIR}/controlnet"
mkdir -p "${MODELS_DIR}/upscale_models"
mkdir -p "${MODELS_DIR}/ipadapter"
mkdir -p "${MODELS_DIR}/clip_vision"
mkdir -p "${MODELS_DIR}/vae"
mkdir -p "${OUTPUT_DIR}"
mkdir -p "${CUSTOM_NODES_DIR}"
mkdir -p "${INPUT_DIR}"

# Make accessible to docker user (UID 1000 in most images)
chown -R 1000:1000 "${COMFYUI_HOME}"

log "Directories created."

# ═════════════════════════════════════════════════════════════════════════════
# STEP 4: Download Models
# ═════════════════════════════════════════════════════════════════════════════

info "Step 4/5: Downloading models from config/model_manifest.yaml..."

python3 -c "
import yaml
import sys
import subprocess
import os

try:
    with open('config/model_manifest.yaml', 'r') as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(f'\\033[0;31m[✗]\\033[0m Error reading manifest: {e}')
    sys.exit(1)

models_dir = '/opt/comfyui/models'
for m in data.get('models', []):
    name = m['name']
    url = m.get('url')
    if not url or url == 'YOUR_LORA_URL_HERE':
        continue
    dest = f\"{models_dir}/{m['destination']}/{m['filename']}\"
    
    if os.path.exists(dest):
        print(f'\\033[0;32m[✓]\\033[0m {name} — already exists, skipping.')
        continue
        
    print(f'\\n\\033[0;34m[→]\\033[0m Downloading {name} (~{m.get(\"size_mb\", \"unknown\")} MB)...')
    cmd = ['curl', '-L', '--progress-bar', '-o', dest, url]
    res = subprocess.run(cmd)
    
    if res.returncode != 0:
        if m.get('required'):
            print(f'\\033[0;31m[✗]\\033[0m Failed to download {name}. This model is required.')
            sys.exit(1)
        else:
            print(f'\\033[1;33m[⚠]\\033[0m Failed to download {name}. Continuing as it is optional.')
    else:
        print(f'\\033[0;32m[✓]\\033[0m {name} — downloaded successfully.')
"
if [ \$? -ne 0 ]; then
    err "Model download failed."
    exit 1
fi

echo ""

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5: Docker Compose File + Start
# ═════════════════════════════════════════════════════════════════════════════

info "Step 5/5: Writing Docker Compose file and starting ComfyUI..."

cat > "${COMFYUI_HOME}/docker-compose.yml" << 'COMPOSE_EOF'
services:
  comfyui:
    image: ghcr.io/ai-dock/comfyui:latest-cuda
    container_name: comfyui
    ports:
      - "8188:8188"
    volumes:
      - ./models:/workspace/ComfyUI/models
      - ./output:/workspace/ComfyUI/output
      - ./custom_nodes:/workspace/ComfyUI/custom_nodes
      - ./input:/workspace/ComfyUI/input
    environment:
      - COMFYUI_ARGS=--listen 0.0.0.0 --port 8188
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8188/system_stats"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
COMPOSE_EOF

cd "${COMFYUI_HOME}"
docker compose up -d

log "ComfyUI container starting..."

# Wait for health check
info "Waiting for ComfyUI to become healthy (can take 60-90 seconds on first start)..."
for i in {1..30}; do
    if curl -sf http://localhost:8188/system_stats > /dev/null 2>&1; then
        log "ComfyUI is up and running!"
        break
    fi
    sleep 5
    echo -n "."
done
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# DONE
# ═════════════════════════════════════════════════════════════════════════════

EXTERNAL_IP=$(curl -sf http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip -H "Metadata-Flavor: Google" 2>/dev/null || echo "<YOUR_VM_IP>")

echo ""
echo "================================================"
echo "  ✅ DharmaPath ComfyUI Setup Complete!"
echo "================================================"
echo ""
echo "  ComfyUI URL:  http://${EXTERNAL_IP}:8188"
echo ""
echo "  Update your .env file:"
echo "    COMFYUI_BASE_URL=http://${EXTERNAL_IP}:8188"
echo ""
echo "  Models directory:  ${MODELS_DIR}"
echo "  Output directory:  ${OUTPUT_DIR}"
echo ""
echo "  Useful commands:"
echo "    docker compose -f ${COMFYUI_HOME}/docker-compose.yml logs -f"
echo "    docker compose -f ${COMFYUI_HOME}/docker-compose.yml restart"
echo "    docker compose -f ${COMFYUI_HOME}/docker-compose.yml down"
echo ""
echo "  ⚠ IMPORTANT: Create a firewall rule to allow TCP:8188 from your IP:"
echo "    gcloud compute firewall-rules create allow-comfyui \\"
echo "      --allow=tcp:8188 \\"
echo "      --source-ranges=<YOUR_IP>/32 \\"
echo "      --target-tags=comfyui-server"
echo ""
echo "  Then add the 'comfyui-server' network tag to your VM instance."
echo ""
