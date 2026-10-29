"""Persona compile/preview/commit over HTTP (roadmap Phase 5.1).

The same operations `scripts/create_friend.py`'s CLI wizard performs,
exposed so the Phase 5.2 web onboarding flow (`frontend/app/onboarding/`)
can drive them without a terminal. Nothing here re-implements the wizard's
logic -- `compile_persona`, `serialize_persona_toml`, and
`scripts.validate_persona_file.validate` are imported and called exactly as
the CLI calls them.

Statelessness, deliberately: `/compile` returns the full compiled persona;
`/commit` takes that same payload back rather than a description to
recompile from. An LLM is not perfectly reproducible, so recompiling on
commit could write something the person never actually previewed. What they
approved is what gets saved.
"""

import dataclasses
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import Config
from ..llm import build_llm_client
from ..llm.model_roles import ModelRole
from ..persona.authoring import IMMUTABLE_CORE
from ..persona.compiler import PersonaCompilationError, compile_persona
from ..persona.profile import PersonaProfile
from ..persona.wizard import serialize_persona_toml

router = APIRouter(prefix="/api/persona", tags=["persona"])


def _app_root() -> Path:
    """Return the writable shared root in Compose or the repository locally."""
    configured = os.environ.get("AI_FRIEND_APP_ROOT")
    if configured:
        return Path(configured)

    discovered = Path(__file__).resolve().parents[3]
    # `/app/app/api/persona.py` has `/` as parents[3] in the image. Keep the
    # fallback useful for direct local execution without relying on cwd.
    return Path("/app") if discovered == Path("/") else discovered


REPO_ROOT = _app_root()


def _configured_path(setting: str, fallback: Path) -> Path:
    value = getattr(Config, setting, None)
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


PERSONA_PATH = _configured_path(
    "PERSONA_PROFILE_PATH", REPO_ROOT / "personal" / "persona.toml"
)
BIOGRAPHY_PATH = _configured_path(
    "BIOGRAPHY_PATH", REPO_ROOT / "personal" / "biography.md"
)
ENV_PATH = Path(os.environ.get("AI_FRIEND_ENV_PATH", str(REPO_ROOT / ".env")))


class CompileRequest(BaseModel):
    description: str = Field(max_length=20_000)


class CommitRequest(BaseModel):
    profile: PersonaProfile
    biography_markdown: str = Field(max_length=100_000)
    force: bool = False


class DryRunChatRequest(BaseModel):
    profile: PersonaProfile
    message: str = Field(max_length=20_000)


def _compiled_persona_payload(compiled) -> dict:
    return {
        "profile": compiled.profile.model_dump(),
        "biography_markdown": compiled.biography_markdown,
        "inferences": [dataclasses.asdict(i) for i in compiled.inferences],
        "dimensions": compiled.dimensions,
        # Never let a client hold its own copy of this -- it's read fresh off
        # `IMMUTABLE_CORE` on every response so a UI showing "IMMUTABLE" next
        # to CONSTITUTIONAL/ADAPTIVE can never drift from the real safety
        # core the way three tracked-file copies once did (ground-truth
        # finding 0.2).
        "immutable_core": IMMUTABLE_CORE,
    }


@router.get("/live")
async def live_persona_endpoint():
    """Who the friend currently is -- read-only, sourced from the durable
    store, not `personal/persona.toml`.

    `authoring.py`'s module docstring is the reason this can't just re-read
    the seed file: it is consulted on first boot only, then inert forever --
    "read once, then never again." Trust, attachment, adaptive traits and
    speaking style all live in the durable store after that and evolve
    through conversation, not through editing a file. Reading the file back
    would show what was *written*, not who the friend has *become*. Mirrors
    `scripts/show_persona.py --json` exactly -- same store, same hydration --
    rather than re-deriving the read.
    """
    from ..cognitive.identity import IdentityManager
    from ..state.conversation_store import ConversationHistoryStore

    store = ConversationHistoryStore()
    try:
        await store.initialize()
        if store.pool is None:
            raise HTTPException(status_code=503, detail="No database reachable")

        identity = IdentityManager()
        await identity.hydrate_from_config_store(store)
        if identity.config_store is None:
            raise HTTPException(
                status_code=503, detail="Could not read the stored persona"
            )

        return {
            "persona": identity.persona.model_dump(),
            "immutable_core": identity.immutable_core,
            "relationship": identity.history.get("relationship", "Friend"),
            "seeded_from_file": identity.history.get(IdentityManager.SEED_MARKER),
        }
    finally:
        await store.close()


@router.post("/compile")
async def compile_persona_endpoint(body: CompileRequest):
    """Prose -> a previewable, validated `PersonaProfile`. Nothing is written."""
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="description is empty")

    client = build_llm_client(base_url=Config.OLLAMA_URL, model=Config.LLM_CHAT_MODEL)
    try:
        compiled = await compile_persona(body.description, llm=client)
    except PersonaCompilationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await client.close()

    return _compiled_persona_payload(compiled)


@router.post("/dry-run-chat")
async def dry_run_chat_endpoint(body: DryRunChatRequest):
    """One line of conversation in a compiled (not yet saved) persona's
    voice -- no memory, no affect, no mesh, matching
    `create_friend.py::_dry_run_chat`'s own scope exactly."""
    profile = body.profile
    system_prompt = (
        f"You are {profile.name}. {profile.base_tone}\n"
        f"{profile.identity_summary}\n"
        f"Traits: {', '.join(profile.traits) or '(none specified)'}.\n"
        f"Never say: {', '.join(profile.avoid) or '(nothing specified)'}.\n"
        "Stay fully in character. This is a short preview conversation."
    )
    client = build_llm_client(base_url=Config.OLLAMA_URL, model=Config.LLM_CHAT_MODEL)
    try:
        reply = await client.generate(
            prompt=body.message,
            system=system_prompt,
            role=ModelRole.REALIZATION,
        )
    finally:
        await client.close()
    return {"reply": reply}


@router.post("/commit")
async def commit_persona_endpoint(body: CommitRequest):
    """Validates, then writes `personal/persona.toml` / `biography.md` and
    points `.env` at them -- the one-way door `authoring.py` consumes on the
    agent's next first boot. Refuses an existing `personal/persona.toml`
    without `force`, exactly like the CLI's own guard."""
    from dotenv import set_key

    from scripts.validate_persona_file import validate

    if PERSONA_PATH.exists() and not body.force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{PERSONA_PATH.relative_to(REPO_ROOT)} already exists -- this "
                "looks like a friend you already have. Pass force=true to "
                "overwrite (cannot be undone)."
            ),
        )

    toml_text = serialize_persona_toml(body.profile)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".toml", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(toml_text)
        temp_path = Path(handle.name)

    try:
        problems = validate(temp_path)
        if problems:
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "The compiled persona did not pass validation; "
                    "nothing was saved. This is a compiler bug.",
                    "problems": problems,
                },
            )

        PERSONA_PATH.parent.mkdir(parents=True, exist_ok=True)
        PERSONA_PATH.write_text(toml_text, encoding="utf-8")
        BIOGRAPHY_PATH.write_text(body.biography_markdown, encoding="utf-8")
    finally:
        temp_path.unlink(missing_ok=True)

    set_key(
        str(ENV_PATH), "PERSONA_PROFILE_PATH", str(PERSONA_PATH.relative_to(REPO_ROOT))
    )
    set_key(str(ENV_PATH), "BIOGRAPHY_PATH", str(BIOGRAPHY_PATH.relative_to(REPO_ROOT)))

    return {
        "status": "saved",
        "persona_path": str(PERSONA_PATH.relative_to(REPO_ROOT)),
        "biography_path": str(BIOGRAPHY_PATH.relative_to(REPO_ROOT)),
    }
