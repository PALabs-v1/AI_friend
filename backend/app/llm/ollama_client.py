import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config import Config
from app.llm.model_roles import ModelRole
from app.llm.role_trace import trace_role
from app.measure_trace import fingerprint as _fingerprint
from app.measure_trace import trace as _measure_trace

logger = logging.getLogger("ollama_client")

# Matches a role-impersonation prefix at the start of a line (system:,
# Assistant :, etc.), case-insensitive, tolerant of leading whitespace and
# spacing around the colon. Used only on the /api/generate fallback path (see
# _build_generate_prompt) -- the primary /api/chat path doesn't have this
# problem since Ollama enforces role separation structurally there.
_ROLE_PREFIX_RE = re.compile(r"(?im)^[ \t]*(system|assistant|user)\s*:\s*")


class OllamaClient:
    """
    Resilient Ollama Client for AI Friend.
    Implements Exponential Backoff with Jitter for high-load reliability.
    Uses httpx for unified async stack and connection pooling.
    """

    _DEFAULT_MODEL = "llama3.2:1b"

    # A production client (brain_agent, subconscious_agent) lives for the
    # process's whole uptime and calls generate/generate_stream on every turn
    # forever; nothing in app/ ever reads request_provenance, only evals/ does.
    # Bounding it keeps a long-running agent's memory flat instead of growing
    # one dict per LLM call for the life of the process, while staying far
    # larger than any single eval run's request count.
    _MAX_REQUEST_PROVENANCE = 500

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434", model: str | None = None
    ):
        self.base_url = base_url.rstrip("/")
        # An explicit `model=None` (e.g. Config.LLM_CHAT_MODEL unset, routed
        # through build_llm_client) must still land on a real model string --
        # passing None explicitly bypasses a plain keyword default entirely.
        self.model = model or self._DEFAULT_MODEL
        self.max_retries = 3
        self.base_delay = 1.0
        self.timeout = httpx.Timeout(10.0, read=180.0, connect=5.0)
        self._client: httpx.AsyncClient | None = None
        # Hash-only request trace consumed by the eval harness.  Keeping it on
        # the client makes the observed endpoint/model variant authoritative,
        # including fallback attempts, rather than reconstructed in eval code.
        self.request_provenance: list[dict[str, Any]] = []
        self.request_provenance_dropped = 0
        # The eval harness sets this around each generation so a request trace
        # can be joined back to the scored probe. Production callers leave it
        # unset; no conversation content is stored here.
        self.request_context: str | None = None

    def _record_request(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        successful: bool = False,
        role: ModelRole | None = None,
    ) -> None:
        messages = payload.get("messages") or []
        prompt_text = str(
            payload.get("prompt")
            or "\n".join(str(message.get("content", "")) for message in messages)
        )
        system_text = next(
            (
                str(message.get("content", ""))
                for message in messages
                if message.get("role") == "system"
            ),
            "",
        )
        self.request_provenance.append(
            {
                "base_url": self.base_url,
                "endpoint": endpoint,
                "model": payload.get("model"),
                "stream": payload.get("stream"),
                "options": dict(payload.get("options") or {}),
                "keep_alive": payload.get("keep_alive"),
                "prompt_sha256": _fingerprint(prompt_text),
                "system_prompt_sha256": _fingerprint(system_text),
                "request_context": self.request_context,
                "role": role.value if role is not None else None,
                "successful": successful,
            }
        )
        if len(self.request_provenance) > self._MAX_REQUEST_PROVENANCE:
            dropped = len(self.request_provenance) - self._MAX_REQUEST_PROVENANCE
            del self.request_provenance[:dropped]
            self.request_provenance_dropped += dropped

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_model_provenance(self, model: str | None = None) -> dict[str, Any]:
        """Return the installed Ollama identity for an eval target.

        The request trace records the tag sent to inference, but a tag alone
        is not a model identity: aliases such as ``phi4-mini`` can resolve to
        an installed ``phi4-mini:latest`` with a particular digest. This
        best-effort metadata call is kept separate from generation so an
        unavailable tags endpoint does not turn a usable eval into a crash.
        """
        target = model or self.model
        result: dict[str, Any] = {"requested_model": target}
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            if response.status_code >= 400:
                result["error"] = f"HTTP {response.status_code}"
                return result
            payload = response.json()
            models = payload.get("models", [])
            variants = set(self._build_model_variants(target))
            match = next(
                (item for item in models if item.get("name") in variants), None
            )
            if not isinstance(match, dict):
                result["installed"] = False
                return result
            result.update(
                {
                    "installed": True,
                    "name": match.get("name"),
                    "digest": match.get("digest"),
                    "size": match.get("size"),
                    "modified_at": match.get("modified_at"),
                    "details": match.get("details", {}),
                }
            )
        except Exception as exc:
            result["error"] = type(exc).__name__
        return result

    def _build_generate_prompt(self, prompt: str, system: str | None = None) -> str:
        """Flatten prompt+system into the single string /api/generate expects.

        C3: this used to strip only the two literal substrings "System:" and
        "Assistant:", trivially bypassed by a different case or spacing
        ("SYSTEM:", " assistant :"). Strips any line-leading role prefix
        instead, case-insensitively, so text the caller supplies can't inject
        a fake turn boundary into the flat prompt. Not a complete defense --
        no regex can be, against a model that reads all of this as one token
        stream -- but it closes the specific bypass the old check had. The
        /api/chat path this client tries first doesn't need this at all: it
        sends system/user as separate structured messages.
        """
        safe_prompt = _ROLE_PREFIX_RE.sub("", prompt)
        return f"{system}\n\nUser: {safe_prompt}\nAssistant:" if system else safe_prompt

    def _build_chat_messages(
        self, prompt: str, system: str | None = None
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_payload_attempts(
        self,
        prompt: str,
        system: str | None,
        model: str,
        stream: bool,
        num_predict: int,
        options_override: dict[str, Any] | None = None,
    ) -> list[tuple[str, dict[str, Any], str]]:
        model_variants = self._build_model_variants(model)

        options = {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": num_predict,
            "num_thread": 6,
            "num_ctx": Config.LLM_NUM_CTX,
        }
        if options_override:
            options.update(options_override)

        attempts: list[tuple[str, dict[str, Any], str]] = []
        for model_variant in model_variants:
            generate_payload = {
                "model": model_variant,
                "prompt": self._build_generate_prompt(prompt, system),
                "stream": stream,
                "options": options,
                "keep_alive": "20m",
            }
            chat_payload = {
                "model": model_variant,
                "messages": self._build_chat_messages(prompt, system),
                "stream": stream,
                "options": options,
                "keep_alive": "20m",
            }
            attempts.append(("/api/chat", chat_payload, model_variant))
            attempts.append(("/api/generate", generate_payload, model_variant))

        return attempts

    def _trace_prompt(self, prompt: str, system: str | None, model: str) -> None:
        """Stage 3 measurement 1.5 (prompt-prefix sharing): records which
        model was called and how big the realized prompt was. The digest is
        cheap and always safe to log once MEASURE_TRACE is on; the literal
        text -- what the harness actually needs to compute a shared prefix
        across a turn's six calls -- is gated separately behind
        MEASURE_TRACE_FULL_PROMPTS, off by default even then.
        """
        if not Config.MEASURE_TRACE:
            return
        realized = self._build_generate_prompt(prompt, system)
        fields: dict[str, object] = {
            "model": model,
            "digest": _fingerprint(realized),
            "length": len(realized),
        }
        if Config.MEASURE_TRACE_FULL_PROMPTS:
            fields["text"] = realized
        _measure_trace("ollama_client", "prompt", **fields)

    def _build_model_variants(self, model: str) -> list[str]:
        variants = [model]
        if ":" not in model:
            variants.append(f"{model}:latest")
        return list(dict.fromkeys(variants))

    @staticmethod
    def _extract_first_memory_snippet(prompt: str) -> str:
        """Return the first surfaced-memory line injected into the prompt, if any.

        Used only by the deterministic ``MOCK_LLM_TEXT`` path. It reflects back
        whatever memory content retrieval actually surfaced, so a passing recall
        test proves retrieval worked — rather than the answer being hardcoded to
        the evaluation corpus. Corpus-agnostic by construction.
        """
        marker = "SHARED HISTORY / RECENT CONTEXT"
        idx = prompt.find(marker)
        if idx == -1:
            return ""
        block = prompt[idx + len(marker) :]
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("-"):
                text = stripped.lstrip("-").strip()
                if text:
                    return text[:160]
        return ""

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        text = payload.get("response")
        if isinstance(text, str):
            return text

        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        return ""

    async def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
        role: ModelRole | None = None,
    ) -> AsyncGenerator[str, None]:
        trace_role("ollama", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            # Corpus-agnostic deterministic mock: reflect back whatever memory
            # content retrieval actually surfaced into the prompt. This exercises
            # retrieval honestly instead of hardcoding the evaluation corpus.
            snippet = self._extract_first_memory_snippet(prompt)
            if snippet:
                yield f"I remember that — {snippet}"
            else:
                yield "I'm thinking about our conversation, my friend."
            return

        self._trace_prompt(prompt, system, model or self.model)
        payload_attempts = self._build_payload_attempts(
            prompt=prompt,
            system=system,
            model=model or self.model,
            stream=True,
            num_predict=40,
            options_override=options_override,
        )

        errors: list[str] = []
        client = await self._get_client()

        for attempt in range(self.max_retries):
            for endpoint, payload, model_variant in payload_attempts:
                try:
                    self._record_request(endpoint, payload, role=role)
                    async with client.stream(
                        "POST", endpoint, json=payload
                    ) as response:
                        if response.status_code >= 400:
                            details = (await response.aread())[:200].decode()
                            errors.append(
                                f"{endpoint} ({model_variant}): HTTP {response.status_code} {details}"
                            )
                            continue

                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                                if chunk.get("done"):
                                    continue
                                text = self._extract_response_text(chunk)
                                if text:
                                    yield text
                            except json.JSONDecodeError:
                                continue
                        self.request_provenance[-1]["successful"] = True
                        return
                except (TimeoutError, httpx.HTTPError) as e:
                    errors.append(f"{endpoint} ({model_variant}): {type(e).__name__}")
                    continue

            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt) + random.uniform(0, 0.5)  # nosec B311 - retry backoff jitter, not cryptographic
                await asyncio.sleep(delay)

        yield "I'm having trouble thinking right now..."

    def _mock_generate_response(self, prompt: str) -> str:
        """Deterministic MOCK_LLM_TEXT reply, keyed on prompt content shape."""
        lower_prompt = prompt.lower()

        # Corpus-agnostic deterministic mock: reflect back the memory content
        # retrieval actually surfaced, rather than hardcoding eval entities.
        snippet = self._extract_first_memory_snippet(prompt)
        if snippet and "shared history / recent context" in lower_prompt:
            return f"I remember that — {snippet}"

        if "subject_type" in lower_prompt or "output json list only" in lower_prompt:
            return json.dumps(
                [
                    {
                        "subject": "User",
                        "subject_type": "person",
                        "relation": "likes",
                        "object": "reading sci-fi",
                        "object_type": "activity",
                        "category": "social",
                        "confidence": 0.9,
                        "reason": "User mentioned reading sci-fi and we had a warm discussion about it.",
                    }
                ]
            )
        elif "new_traits" in lower_prompt or "relationship" in lower_prompt:
            return json.dumps(
                {"new_traits": [], "relationship": "friend", "confidence": 0.9}
            )
        elif (
            "goal_congruence" in lower_prompt or "appraisal dimensions" in lower_prompt
        ):
            return json.dumps(
                {"goal_congruence": 0.0, "norm_alignment": 1.0, "expectedness": 0.5}
            )
        elif "inferred_valence" in lower_prompt or "implied_goals" in lower_prompt:
            return json.dumps(
                {
                    "intent": "CHAT",
                    "goal": "socialize",
                    "inferred_valence": 0.5,
                    "inferred_arousal": 0.3,
                    "implied_goals": ["chat_socially"],
                }
            )
        elif "consolidate" in lower_prompt or "episodic memory summary" in lower_prompt:
            return "We discussed our shared interests, including sci-fi books and coding algorithms, and enjoyed a friendly conversation."
        elif "dream" in lower_prompt:
            return "Processing memories of my friend, feeling a deep sense of connection through shared projects and programming ideas."
        elif "thought" in lower_prompt or "inner monologue" in lower_prompt:
            return "I appreciate my friend. I wonder what they are coding today."
        else:
            return "I am glad we are chatting, my friend. What should we work on next?"

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        options_override: dict[str, Any] | None = None,
        role: ModelRole | None = None,
    ) -> str:
        trace_role("ollama", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            return self._mock_generate_response(prompt)

        self._trace_prompt(prompt, system, model or self.model)
        payload_attempts = self._build_payload_attempts(
            prompt=prompt,
            system=system,
            model=model or self.model,
            stream=False,
            num_predict=64,
            options_override=options_override,
        )

        client = await self._get_client()
        errors: list[str] = []

        for attempt in range(self.max_retries):
            for endpoint, payload, model_variant in payload_attempts:
                try:
                    self._record_request(endpoint, payload, role=role)
                    response = await client.post(endpoint, json=payload)
                    if response.status_code >= 400:
                        errors.append(f"{endpoint}: HTTP {response.status_code}")
                        continue

                    result = response.json()
                    text = self._extract_response_text(result)
                    if text:
                        self.request_provenance[-1]["successful"] = True
                        return text
                except (TimeoutError, httpx.HTTPError) as e:
                    errors.append(f"{endpoint}: {type(e).__name__}")
                    continue

            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt) + random.uniform(0, 0.5)  # nosec B311 - retry backoff jitter, not cryptographic
                await asyncio.sleep(delay)

        return "Error generating response."

    async def describe_image(
        self,
        image_b64: str,
        prompt: str = "What do you see?",
        model: str | None = None,
        role: ModelRole | None = None,
    ) -> str | None:
        """Returns the VLM's description, `""` for a genuinely quiet scene,
        or `None` if the call itself failed (H8). The two empty-ish outcomes
        used to both come back as `""`, indistinguishable to the caller even
        though the log line already told them apart - `VisualAppraisalService`
        falls back to its cached description either way, so this only
        changes what a caller *can* tell, not what it currently does with it.
        """
        trace_role("ollama", role, model or self.model)
        if getattr(Config, "MOCK_LLM_TEXT", False):
            ai_name = getattr(Config, "AI_NAME", "AI Friend")
            return f"A user sitting at a desk pair-programming with their AI friend {ai_name}."

        target_model = model or self.model
        payload = {
            "model": target_model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 128},
            "keep_alive": "10m",
        }

        client = await self._get_client()
        try:
            response = await client.post("/api/generate", json=payload, timeout=120.0)
            if response.status_code == 200:
                return self._extract_response_text(response.json())
            logger.warning(
                "[Ollama] Vision generate returned HTTP %s for model %s; "
                "returning no description.",
                response.status_code,
                target_model,
            )
        except Exception as exc:
            # Logged rather than swallowed. The empty string returned below is
            # indistinguishable from "the model saw nothing worth describing",
            # so without this line a vision backend that is down looks exactly
            # like a quiet room -- and the agent narrates the difference to the
            # user as if it were real.
            logger.warning(
                "[Ollama] Vision generate failed for model %s (%s); "
                "returning no description.",
                target_model,
                exc,
            )
        return None

    async def check_health(self) -> bool:
        client = await self._get_client()
        try:
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False
