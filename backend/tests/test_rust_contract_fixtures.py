import json
import re
from pathlib import Path

from app.contracts import (
    AudioPerception,
    AudioPlaybackLifecycle,
    AudioStop,
    AudioStreamTrailer,
    ChatInput,
    ChatOutput,
    SpeechExpressionWire,
    Topics,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "crates" / "contracts" / "fixtures"


def _load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_rust_chat_output_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("chat_output_chunk.json")

    parsed = ChatOutput.model_validate(payload)

    assert parsed.content == "Hey there<pause=20ms>friend"
    assert parsed.done is False
    assert parsed.turn_id == "turn-1"
    assert parsed.affect.valence == 0.8
    assert parsed.affect.arousal == 0.7
    assert parsed.affect.dominance == 0.6
    assert parsed.affect.trust == 0.5
    assert parsed.affect.attachment == 0.1
    assert parsed.affect.emotion == "happy"
    assert parsed.full_response is None
    assert parsed.generation_error is None
    assert parsed.proactive is False
    # Phase 3B: the shared fixture predates `expression` and has no producer
    # for it yet -- absent must parse as None, not raise, matching the Rust
    # struct's own `#[serde(default)]` on the same field (see
    # `chat_output_expression_defaults_to_none_when_absent` in
    # crates/contracts/src/lib.rs, same fixture).
    assert parsed.expression is None


def test_rust_audio_stop_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("audio_stop_speculative.json")

    parsed = AudioStop.model_validate(payload)

    assert parsed.interrupt is True
    assert parsed.speculative is True
    assert parsed.intent == "SPECULATIVE_STOP"
    assert parsed.intent_type == "VOICE_INTERRUPTION"
    assert parsed.keywords == ["stop"]
    assert parsed.confidence == 0.9
    assert parsed.perception_text == "stop"
    assert parsed.utterance_id == "utt-1"
    assert parsed.flush is False


def test_rust_audio_playback_lifecycle_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("audio_playback_lifecycle_started.json")
    parsed = AudioPlaybackLifecycle.model_validate(payload)

    assert parsed.utterance_id == "utt-1"
    assert parsed.turn_id == "turn-1"
    assert parsed.seq == 0
    assert parsed.state == "STARTED"
    assert parsed.words_played == 0
    assert parsed.words_streamed == 4
    assert parsed.heard_offset == 0
    assert parsed.streamed_offset == 22


def test_rust_audio_stream_trailer_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("audio_stream_trailer.json")
    parsed = AudioStreamTrailer.model_validate(payload)
    assert parsed.kind == "END_OF_STREAM"
    assert parsed.utterance_id == "utt-1"
    assert parsed.turn_id == "turn-1"
    assert parsed.failed is False


def test_python_and_rust_topic_constants_have_exact_parity():
    rust_source = (FIXTURE_DIR.parent / "src" / "lib.rs").read_text(encoding="utf-8")
    rust_topics = set(
        re.findall(
            r'pub const [A-Z0-9_]+: &str = "([^"]+)";',
            rust_source.split("pub const HEADER_LATENCY_META", 1)[0],
        )
    )
    assert rust_topics == {topic.value for topic in Topics}


def test_rust_audio_perception_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("audio_perception_speculative.json")

    parsed = AudioPerception.model_validate(payload)

    assert parsed.text == "stop"
    assert parsed.intent == "SPECULATIVE_STOP"
    assert parsed.intent_type == "COMMAND"
    assert parsed.keywords == ["stop"]
    assert parsed.confidence == 0.9
    assert parsed.snr == 12.5
    assert parsed.speculative_intent.name == "SPECULATIVE_STOP"
    assert parsed.speculative_intent.utterance_id == "utt-1"
    assert parsed.metadata["text"] == "stop"


def test_chat_output_expression_round_trips_when_present():
    """`expression` is optional and `extra: "allow"` on both sides -- a
    populated value (once a Phase 3A producer exists) must still validate
    and round-trip, not just the absent case the fixture-based test above
    covers."""
    payload = _load_fixture("chat_output_chunk.json")
    payload["expression"] = {
        "affect_label": "warm",
        "breath": 0.4,
        "hesitation": 0.6,
        "style": "soft",
        # Stage 3 (Blocker 1): a JSON array of 4-element arrays, the actual
        # shape `generate_apra_trajectory`/`SpeechExpression.trajectory`
        # produce -- not a flat float list.
        "trajectory": [[0, 0.95, 1.1, 0.1], [17, 0.96, 1.1, 0.11]],
    }

    parsed = ChatOutput.model_validate(payload)

    assert isinstance(parsed.expression, SpeechExpressionWire)
    assert parsed.expression.affect_label == "warm"
    assert parsed.expression.breath == 0.4
    assert parsed.expression.hesitation == 0.6
    assert parsed.expression.style == "soft"
    assert parsed.expression.trajectory == [(0, 0.95, 1.1, 0.1), (17, 0.96, 1.1, 0.11)]


def test_chat_output_expression_trajectory_accepts_real_apra_frame_shape():
    """Stage 3 (Blocker 1, cross-boundary regression Codex's Stage 2 review
    asked for): validates against the *actual* Phase 3A producer output --
    `cognitive.expression.derive_speech_expression`'s `SpeechExpression.
    trajectory`, itself `cognitive_rust.generate_apra_trajectory`'s native
    frame tuples -- not just a same-shaped literal. `SpeechExpressionWire.
    trajectory: list[float]` rejected every frame here with `Input should be
    a valid number`; `list[tuple[int, float, float, float]]` must accept it
    and round-trip it unchanged.
    """
    from app.cognitive.expression import derive_speech_expression

    expression = derive_speech_expression(
        {
            "valence": 0.2,
            "arousal": 0.6,
            "dominance": 0.4,
            "fatigue": 0.1,
            "cortisol": 0.2,
            "dopamine": 0.3,
            "adrenaline": 0.0,
        }
    )

    wire = SpeechExpressionWire.model_validate(expression.model_dump())

    assert wire.trajectory == expression.trajectory
    assert len(wire.trajectory) == 60

    round_tripped = SpeechExpressionWire.model_validate(
        json.loads(json.dumps(wire.model_dump()))
    )
    assert round_tripped.trajectory == wire.trajectory


def test_rust_chat_input_fixture_matches_current_pydantic_contract():
    payload = _load_fixture("chat_input_final.json")

    parsed = ChatInput.model_validate(payload)

    assert parsed.text == "hello there"
    assert parsed.utterance_id == "utt-1"
    assert parsed.metadata.source == "whisper"
    assert parsed.metadata.confidence == 0.9
    assert parsed.metadata.utterance_id == "utt-1"
    assert parsed.latency_metadata["source"] == "transport_agent"
