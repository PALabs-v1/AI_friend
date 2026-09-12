# Installation & Multi-Platform Setup

AI Friend runs as a self-hosted, local-first multi-agent cognitive mesh. It is designed to run entirely on your own workstation or home server with zero mandatory cloud dependencies across **macOS**, **Windows 10/11**, and **Linux**.

---

## 1. Automated One-Command Installer (Recommended)

Choose your operating system to download, configure, and install the complete stack with a single command:

### macOS (Apple Silicon & Intel)
Open Terminal and run:
```bash
curl -fsSL https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.sh | bash
```

### Windows 10/11 (Native PowerShell & WSL2)
Open PowerShell as Administrator and run:
```powershell
irm https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.ps1 | iex
```

### Linux (Ubuntu / Debian / Arch / Fedora)
Open your terminal and run:
```bash
curl -fsSL https://raw.githubusercontent.com/PALabs-v1/AI_friend/main/scripts/install.sh | bash
```

> [!NOTE]
> The automated installer configures `.env`, initializes Python virtual environments, provisions CC0-licensed default voice samples, pulls required Ollama models, and installs the global **`friend`** command in your `PATH`.

---

## 2. Interactive Environment Setup (`friend init`)

During initial installation (or at any time later), run `friend init` to configure your environment:

```bash
friend init
```

The interactive wizard asks you for:
1. **Runtime Environment**: `development` or `production`.
2. **Companion & User Identity**: What your friend should call you, and your friend's name.
3. **Model Selection (Local or Cloud)**:
   - **Local Engine**: Enter ANY local Ollama model tag or fine-tuned GGUF weights.
   - **Cloud Provider**: Enter your preferred provider (Anthropic, OpenAI, OpenRouter) and API key.
4. **Security Passwords**: Automatically generates cryptographically secure random credentials for local Docker databases (Postgres, Neo4j, LiveKit, Session JWTs).
5. **Launch Profile**: Choose `full` (voice + brain), `light` (cognitive-only), or `heavy` (local Whisper STT).

---

## 3. Manual Git Clone & Setup

If you prefer setting up manually without the automated script:

```bash
# 1. Clone the repository
git clone https://github.com/PALabs-v1/AI_friend.git
cd AI_friend

# 2. Configure environment defaults
cp .env.example .env

# 3. Create Python virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e backend

# 4. Provision default voice samples
python backend/scripts/bootstrap/ensure_default_voice_sample.py

# 5. Launch the full mesh
./start.sh
```

On Windows, use `.\start.ps1` or double-click `start.bat`.

---

## 4. The Unified `friend` CLI Tool

After installation, the **`friend`** command manages your companion:

```bash
friend start                # Launch the full 9-agent cognitive mesh
friend start light          # Cognitive-only mode (low memory footprint)
friend start --vision       # Launch with Moondream VLM camera/screen appraisal
friend stop                 # Shut down all background containers
friend status               # Print live container health, memory, and model residency
friend talk                 # Open interactive terminal conversation REPL
friend persona              # Author your friend's personality in natural prose
friend voice                # Enroll an 8-second custom voice sample
friend backup export        # Export complete 4-store state archive (.tar.gz)
friend backup import --file snapshot.tar.gz --force # Restore friend archive
friend logs [service]       # View live logs (e.g. friend logs brain_agent)
friend update               # Pull latest GitHub updates and rebuild
```

---

## 5. System Requirements

| Specification | Minimum Tier (CPU Baseline) | Recommended Tier (Apple Silicon / GPU) |
| :--- | :--- | :--- |
| **Operating System** | macOS 13+, Linux (Ubuntu 22.04+), Windows 10/11 (WSL2 / Docker Desktop) | macOS 14+ (M1/M2/M3/M4), Linux with NVIDIA GPU (CUDA 12+) |
| **RAM / Unified Memory** | 8 GB Host RAM (with Cloud LLM fallback) or 16 GB | 16 GB - 32 GB Unified Memory or 8GB+ Dedicated VRAM |
| **Processor** | 8-core modern x86_64 CPU (AVX2 / AVX-512) | Apple Silicon M-Series or 8-core CPU + NVIDIA RTX 3060/4060+ |
| **Storage** | 25 GB free disk space (models + databases) | 50 GB fast NVMe SSD storage |
| **Network** | Loopback only (zero external internet required after setup) | Loopback only |

---

Next: [Quickstart Guide](/docs/getting-started/quickstart) to author your friend's persona.
