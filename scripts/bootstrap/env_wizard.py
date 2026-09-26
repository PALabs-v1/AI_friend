#!/usr/bin/env python3
"""AI Friend Interactive Environment Setup Wizard ('friend init').

Guides the user step-by-step through configuring all required .env fields:
1. Environment mode (development vs production)
2. Auto-generated cryptographically secure passwords (Postgres, Neo4j, LiveKit, JWT)
3. Model Selection (Local Ollama with any model tag, or Cloud API with keys)
4. Companion Name & User Profile
5. Voice & Hardware operational mode
"""

import secrets
import string
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def generate_secure_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def prompt_user(
    question: str, default: str = "", choices: list[str] | None = None
) -> str:
    choice_str = f" [{'/'.join(choices)}]" if choices else ""
    default_str = f" (default: {default})" if default else ""
    prompt_text = (
        f"\033[1;36m?\033[0m \033[1m{question}\033[0m{choice_str}{default_str}: "
    )

    try:
        val = input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup aborted.")
        sys.exit(1)

    if not val and default:
        return default
    if choices and val not in choices:
        print(
            f"  \033[33mInvalid choice. Using default: {default or choices[0]}\033[0m"
        )
        return default or choices[0]
    return val


def prompt_secret(question: str, default: str = "") -> str:
    default_str = (
        " (press Enter to auto-generate secure key)"
        if not default
        else f" (press Enter to use: {default})"
    )
    prompt_text = f"\033[1;36m?\033[0m \033[1m{question}\033[0m{default_str}: "
    try:
        val = input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup aborted.")
        sys.exit(1)
    if not val:
        return default or generate_secure_password()
    return val


def run_init_wizard(target_env_path: Path | None = None) -> int:
    env_file = target_env_path or (REPO_ROOT / ".env")

    print(
        "\n\033[1;32m═════════════════════════════════════════════════════════════════\033[0m"
    )
    print(
        "\033[1m            AI FRIEND — ENVIRONMENT SETUP WIZARD                 \033[0m"
    )
    print(
        "\033[1;32m═════════════════════════════════════════════════════════════════\033[0m"
    )
    print(
        "This wizard will configure your \033[1m.env\033[0m file with secure credentials,"
    )
    print("your chosen LLM model (local or cloud), and friend preferences.\n")

    # Step 1: Environment Mode
    print("\033[1;34m[1/6] Environment Mode\033[0m")
    env_mode = prompt_user(
        "Choose runtime environment mode",
        default="development",
        choices=["development", "production"],
    )

    # Step 2: Companion & User Identity
    print("\n\033[1;34m[2/6] Companion & User Identity\033[0m")
    friend_name = prompt_user("What should your friend call you?", default="Friend")
    companion_name = prompt_user("What is your friend's name?", default="Maya")

    # Step 3: LLM Model Selection (Local or Cloud)
    print("\n\033[1;34m[3/6] LLM Brain & Model Selection\033[0m")
    print(
        "AI Friend is model-agnostic. You can run any local Ollama model or Cloud API."
    )
    provider_type = prompt_user(
        "Select LLM inference engine",
        default="local",
        choices=["local", "cloud"],
    )

    llm_provider = "ollama"
    anthropic_key = ""
    openai_key = ""
    openrouter_key = ""
    chat_model = "llama3.2:3b"

    if provider_type == "local":
        llm_provider = "ollama"
        print("\nLocal Ollama Engine:")
        print(
            "  • Type ANY model tag (e.g. llama3.2:3b, qwen2.5:7b, deepseek-r1:7b, llama3.2:1b, mistral:7b)"
        )
        chat_model = prompt_user("Enter local model name", default="llama3.2:3b")
    else:
        cloud_vendor = prompt_user(
            "Select cloud provider",
            default="anthropic",
            choices=["anthropic", "openai", "openrouter"],
        )
        llm_provider = cloud_vendor
        if cloud_vendor == "anthropic":
            chat_model = prompt_user(
                "Enter Anthropic model", default="claude-3-5-sonnet-20241022"
            )
            anthropic_key = prompt_secret("Enter ANTHROPIC_API_KEY")
        elif cloud_vendor == "openai":
            chat_model = prompt_user("Enter OpenAI model", default="gpt-4o")
            openai_key = prompt_secret("Enter OPENAI_API_KEY")
        elif cloud_vendor == "openrouter":
            chat_model = prompt_user(
                "Enter OpenRouter model", default="meta-llama/llama-3.3-70b-instruct"
            )
            openrouter_key = prompt_secret("Enter OPENROUTER_API_KEY")

    # Step 4: Security Credentials & Database Passwords
    print("\n\033[1;34m[4/6] Security & Database Credentials\033[0m")
    print("Auto-generating secure random credentials for local Docker databases...")

    postgres_pass = generate_secure_password(24)
    neo4j_pass = generate_secure_password(24)
    livekit_key = "LK_" + generate_secure_password(16)
    livekit_secret = generate_secure_password(32)
    session_secret = generate_secure_password(32)
    nats_users = {
        "PROVISIONER": "nats_provisioner",
        "SIGNALING": "signaling",
        "BRAIN": "brain_agent",
        "SUBCONSCIOUS": "subconscious_agent",
        "SURFACING": "surfacing_agent",
        "SYSTEM": "system_agent",
        "TRANSPORT": "transport_agent",
        "STT": "stt_agent",
        "VISION": "vision_agent",
        "VOICE": "voice_agent",
    }
    nats_env = "\n".join(
        f"NATS_{role}_USER={username}\n"
        f"NATS_{role}_PASSWORD={generate_secure_password(32)}"
        for role, username in nats_users.items()
    )

    # Step 5: Audio & Hardware Operational Profile
    print("\n\033[1;34m[5/6] Operational Launch Mode\033[0m")
    launch_mode = prompt_user(
        "Choose default launch profile",
        default="full",
        choices=["full", "light", "heavy"],
    )

    # Step 6: Vision & Visual Appraisal
    print("\n\033[1;34m[6/6] Visual Appraisal & Vision Awareness\033[0m")
    print(
        "Enables camera and screen understanding with Moondream VLM & biological habituation."
    )
    enable_vision = prompt_user(
        "Enable Visual Appraisal?",
        default="no",
        choices=["yes", "no", "y", "n"],
    )
    is_vision_enabled = enable_vision.lower() in ("yes", "y")

    required_models = f"{chat_model},nomic-embed-text"
    if is_vision_enabled:
        required_models += ",moondream"

    # Construct the validated .env file content
    env_content = f"""# ==============================================================================
#  AI FRIEND — GENERATED ENVIRONMENT CONFIGURATION
#  Generated via: friend init / env_wizard.py
# ==============================================================================

ENVIRONMENT={env_mode}
USER_NAME={friend_name}
COMPANION_NAME={companion_name}

# --- Database & Infrastructure Security ---
POSTGRES_PASSWORD={postgres_pass}
DATABASE_URL=postgresql://ai_friend:{postgres_pass}@127.0.0.1:5432/ai_friend_db
DIRECT_URL=postgresql://ai_friend:{postgres_pass}@127.0.0.1:5432/ai_friend_db

NEO4J_PASSWORD={neo4j_pass}
NEO4J_AUTH=neo4j/{neo4j_pass}

LIVEKIT_API_KEY={livekit_key}
LIVEKIT_API_SECRET={livekit_secret}
LIVEKIT_KEYS="{livekit_key}: {livekit_secret}"
LIVEKIT_URL=ws://livekit:7880
LIVEKIT_PUBLIC_URL=ws://127.0.0.1:7880

SESSION_SECRET={session_secret}

# --- LLM Brain Configuration ---
LLM_PROVIDER={llm_provider}
LLM_CHAT_MODEL={chat_model}
OLLAMA_REQUIRED_MODELS={required_models}

# --- Visual Appraisal & VLM ---
ENABLE_VISION={"true" if is_vision_enabled else "false"}
"""
    if is_vision_enabled:
        env_content += "VLM_MODEL=moondream\n"

    if anthropic_key:
        env_content += f"ANTHROPIC_API_KEY={anthropic_key}\n"
    if openai_key:
        env_content += f"OPENAI_API_KEY={openai_key}\n"
    if openrouter_key:
        env_content += f"OPENROUTER_API_KEY={openrouter_key}\n"

    env_content += f"""
# --- Messaging & Mesh ---
NATS_URL=nats://127.0.0.1:4222
{nats_env}

# --- Default Voice Settings ---
VOICE_SAMPLE_RATE=32000
REF_AUDIO_PATH=backend/voice_samples/default_voice.wav
REF_TEXT="Hello, I am your friend."
DEFAULT_LAUNCH_MODE={launch_mode}
"""

    env_file.write_text(env_content, encoding="utf-8")

    print(
        "\n\033[1;32m═════════════════════════════════════════════════════════════════\033[0m"
    )
    print(f"\033[1;32m✓ Successfully configured environment file at: {env_file}\033[0m")
    print(
        "\033[1;32m═════════════════════════════════════════════════════════════════\033[0m"
    )
    print(f"• Environment Mode:   \033[1m{env_mode}\033[0m")
    print(
        f"• LLM Provider:       \033[1m{llm_provider}\033[0m (\033[1m{chat_model}\033[0m)"
    )
    print(
        f"• Companion / User:   \033[1m{companion_name}\033[0m & \033[1m{friend_name}\033[0m"
    )
    print(
        "• Database Security:  \033[1m✓ Cryptographically secure passwords generated\033[0m"
    )
    print(f"• Default Mode:       \033[1m{launch_mode}\033[0m")
    print("─────────────────────────────────────────────────────────────────\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_init_wizard())
