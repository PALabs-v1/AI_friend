#!/bin/bash
# ==============================================================================
#  AI Friend — Automated Multi-Platform Installer (macOS & Linux)
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.sh | bash
# ==============================================================================
set -euo pipefail

# ── Styling & Colors ──────────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() { echo -e "${CYAN}==>${NC} ${BOLD}$1${NC}"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
log_error() { echo -e "${RED}✗ ERROR: $1${NC}" >&2; }

# ── Banner ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "      _    ___   _____ ____  ___ _____ _   _ ____  "
echo "     / \  |_ _| |  ___|  _ \|_ _| ____| \ | |  _ \ "
echo "    / _ \  | |  | |_  | |_) || ||  _| |  \| | | | |"
echo "   / ___ \ | |  |  _| |  _ < | || |___| |\  | |_| |"
echo "  /_/   \_\___| |_|   |_| \_\___|_____|_| \_|____/ "
echo -e "${NC}"
echo -e "${DIM}  An embodied, local-first lifelong companion on your own hardware.${NC}\n"

# ── Parse Arguments ──────────────────────────────────────────────────────────
TARGET_DIR="${HOME}/AI_friend"
AUTO_START=true
DRY_RUN=false
DEV_MODE=false
MODEL_CHOICE="llama3.2:3b"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      TARGET_DIR="$2"
      shift 2
      ;;
    --model)
      MODEL_CHOICE="$2"
      shift 2
      ;;
    --dev|--source)
      DEV_MODE=true
      shift
      ;;
    --no-start)
      AUTO_START=false
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      echo "Usage: install.sh [--dir <path>] [--model <name>] [--dev] [--no-start] [--dry-run]"
      echo "  --dir <path>   Target installation directory (default: ~/AI_friend)"
      echo "  --model <name> Default LLM model (default: llama3.2:3b, or qwen2.5:7b, deepseek-r1:7b)"
      echo "  --dev          Download full developer monorepo (website, evals, benchmarks)"
      echo "  --no-start     Install and configure without starting the mesh"
      echo "  --dry-run      Check system requirements without making modifications"
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

# ── Platform Detection ───────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
log_info "Detected Platform: ${OS} (${ARCH})"

case "${OS}" in
  Darwin)
    PLATFORM_NAME="macOS"
    if [[ "${ARCH}" == "arm64" ]]; then
      HW_ACCEL="Apple Silicon Metal & Neural Engine"
    else
      HW_ACCEL="Intel x86_64"
    fi
    ;;
  Linux)
    PLATFORM_NAME="Linux"
    if command -v nvidia-smi >/dev/null 2>&1; then
      HW_ACCEL="NVIDIA CUDA Acceleration"
    else
      HW_ACCEL="Standard CPU (AVX-512 / OpenVINO)"
    fi
    ;;
  *)
    log_error "Unsupported operating system: ${OS}. Please use install.ps1 on Windows."
    exit 1
    ;;
esac

log_success "Platform target: ${PLATFORM_NAME} (${HW_ACCEL})"

# ── Prerequisite Validation ──────────────────────────────────────────────────
log_info "Verifying core system prerequisites..."

# 1. Check Git
if ! command -v git >/dev/null 2>&1 && [[ "$DEV_MODE" == true ]]; then
  log_warn "Git is required for developer mode."
  if [[ "${OS}" == "Darwin" ]]; then
    xcode-select --install || true
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y git
  fi
fi

# 2. Check Python 3.11+
PYTHON_CMD=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    MAJOR=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
    MINOR=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
    if [[ "$MAJOR" -ge 3 ]] && [[ "$MINOR" -ge 11 ]]; then
      PYTHON_CMD="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON_CMD" ]]; then
  log_warn "Python 3.11+ is required for the friend CLI."
  if [[ "${OS}" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    echo "Installing Python via Homebrew..."
    brew install python@3.12
    PYTHON_CMD="python3.12"
  else
    log_error "Please install Python 3.11+ (https://www.python.org/downloads/)."
    exit 1
  fi
fi
log_success "Python 3.11+ available (${PYTHON_CMD})"

# 3. Check Docker
if ! command -v docker >/dev/null 2>&1; then
  log_warn "Docker is not installed."
  echo "Docker Desktop is required to run the 9-agent container mesh without local build tools."
  echo "Download Docker: https://www.docker.com/products/docker-desktop"
  if [[ "$DRY_RUN" == true ]]; then
    exit 0
  fi
fi

# 4. Check Ollama
if ! command -v ollama >/dev/null 2>&1; then
  log_warn "Ollama is recommended for local LLM inference."
  echo "You can install Ollama via: curl -fsSL https://ollama.com/install.sh | sh"
fi

if [[ "$DRY_RUN" == true ]]; then
  log_success "Dry run checks complete! System is compatible."
  exit 0
fi

# ── Download Runtime Bundle vs Full Monorepo ─────────────────────────────────
mkdir -p "$TARGET_DIR"

if [[ "$DEV_MODE" == true ]]; then
  log_info "Downloading full developer repository into ${TARGET_DIR}..."
  if [[ -d "$TARGET_DIR/.git" ]]; then
    cd "$TARGET_DIR" && git pull --ff-only
  else
    git clone https://github.com/PALabs-v1/AI_friend.git "$TARGET_DIR"
    cd "$TARGET_DIR"
  fi
else
  log_info "Downloading lightweight runtime bundle (~4.3 MB)..."
  TAR_URL="https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/dist/ai-friend-runtime.tar.gz"
  FALLBACK_URL="https://github.com/PALabs-v1/AI_friend/archive/refs/heads/main.tar.gz"

  TMP_ARCHIVE="/tmp/ai-friend-runtime.tar.gz"
  if curl -fsSL "$TAR_URL" -o "$TMP_ARCHIVE" 2>/dev/null; then
    tar -xzf "$TMP_ARCHIVE" -C "$TARGET_DIR" --strip-components=1 2>/dev/null || tar -xzf "$TMP_ARCHIVE" -C "$TARGET_DIR"
    rm -f "$TMP_ARCHIVE"
    cd "$TARGET_DIR"
  else
    # Fallback to shallow clone
    log_info "Fetching shallow runtime checkout..."
    git clone --depth 1 https://github.com/PALabs-v1/AI_friend.git "$TARGET_DIR"
    cd "$TARGET_DIR"
  fi
fi

# ── Environment & Model Setup ────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  if [[ -t 0 ]] && [[ -f scripts/bootstrap/env_wizard.py ]]; then
    log_info "Launching interactive environment setup wizard..."
    "$PYTHON_CMD" scripts/bootstrap/env_wizard.py || cp .env.example .env
  elif [[ -f .env.example ]]; then
    cp .env.example .env
  else
    touch .env
  fi
fi

# Set active chat model in .env if explicitly passed
if [[ -n "$MODEL_CHOICE" ]] && [[ "$MODEL_CHOICE" != "llama3.2:3b" ]]; then
  if grep -q "^LLM_CHAT_MODEL=" .env 2>/dev/null; then
    sed -i.bak "s/^LLM_CHAT_MODEL=.*/LLM_CHAT_MODEL=${MODEL_CHOICE}/" .env && rm -f .env.bak
  else
    echo "LLM_CHAT_MODEL=${MODEL_CHOICE}" >> .env
  fi
fi

# Setup Python Virtual Environment
if [[ ! -d .venv ]]; then
  log_info "Setting up Python virtual environment..."
  "$PYTHON_CMD" -m venv .venv
fi

# Install dependencies if backend exists
if [[ -f backend/pyproject.toml ]]; then
  .venv/bin/pip install -e backend >/dev/null 2>&1 || true
fi

# Provision Default CC0 Voice Samples
if [[ -f backend/scripts/bootstrap/ensure_default_voice_sample.py ]]; then
  .venv/bin/python backend/scripts/bootstrap/ensure_default_voice_sample.py >/dev/null 2>&1 || true
fi

# ── Install Global `friend` CLI ──────────────────────────────────────────────
log_info "Installing global 'friend' CLI command..."

BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

cat << EOF > "${BIN_DIR}/friend"
#!/bin/bash
REPO_ROOT="${TARGET_DIR}"
PYTHON_EXEC="\${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "\$PYTHON_EXEC" ]]; then
  PYTHON_EXEC="${PYTHON_CMD}"
fi

exec "\$PYTHON_EXEC" "\${REPO_ROOT}/scripts/friend_cli.py" "\$@"
EOF

chmod +x "${BIN_DIR}/friend"

# Ensure ~/.local/bin is in PATH
SHELL_NAME="$(basename "${SHELL:-bash}")"
RC_FILE="${HOME}/.bashrc"
[[ "$SHELL_NAME" == "zsh" ]] && RC_FILE="${HOME}/.zshrc"

if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
  if [[ -f "$RC_FILE" ]] && ! grep -q '.local/bin' "$RC_FILE" 2>/dev/null; then
    echo -e '\nexport PATH="$HOME/.local/bin:$PATH"' >> "$RC_FILE"
  fi
  export PATH="${BIN_DIR}:$PATH"
fi
log_success "Global command installed: ${BIN_DIR}/friend"

# ── Summary & Next Steps ─────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}             AI FRIEND INSTALLATION COMPLETE!                    ${NC}"
echo -e "${GREEN}═════════════════════════════════════════════════════════════════${NC}"
echo -e "Install Directory: ${BOLD}${TARGET_DIR}${NC} (~4.3 MB runtime)"
echo -e "Selected Model:    ${BOLD}${MODEL_CHOICE}${NC}"
echo -e "Commands:"
echo -e "  ${CYAN}friend start${NC}            Start the 9-agent cognitive mesh"
echo -e "  ${CYAN}friend model list${NC}       Browse, pull, and switch LLM models"
echo -e "  ${CYAN}friend talk${NC}             Open terminal conversation REPL"
echo -e "  ${CYAN}friend persona${NC}          Author your friend's personality"
echo -e "  ${CYAN}friend voice${NC}            Enroll an 8-second custom voice"
echo -e "  ${CYAN}friend status${NC}           Inspect live agent health & RAM"
echo -e "  ${CYAN}friend stop${NC}             Shut down all mesh services"
echo -e "${GREEN}─────────────────────────────────────────────────────────────────${NC}\n"

if [[ "$AUTO_START" == true ]]; then
  read -r -p "Would you like to start AI Friend right now? [Y/n] " RESPONSE || RESPONSE="y"
  RESPONSE="${RESPONSE,,}"
  if [[ "$RESPONSE" =~ ^(yes|y|)$ ]]; then
    exec "${TARGET_DIR}/start.sh"
  fi
fi
