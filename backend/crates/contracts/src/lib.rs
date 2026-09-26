use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub mod topics {
    pub const CHAT_INPUT: &str = "chat.input";
    pub const CHAT_OUTPUT: &str = "chat.output";
    pub const AUDIO_PERCEPTION: &str = "audio.perception";
    pub const AUDIO_STOP: &str = "audio.stop";
    pub const AUDIO_RESUME: &str = "audio.resume";
    pub const AUDIO_INBOUND: &str = "audio.inbound";
    pub const AUDIO_STREAM: &str = "audio.stream";
    pub const VISION_CONTROL: &str = "vision.control";
    pub const VISION_FRAMES: &str = "vision.frames";
    pub const VISION_DESCRIPTION: &str = "vision.description";
    pub const VISION_FACIAL_REFLEX: &str = "vision.facial_reflex";
    pub const VOICE_SEGMENTATION_FEEDBACK: &str = "voice.segmentation_feedback";
    pub const SYSTEM_TICK: &str = "system.tick";
    pub const MEMORY_SURFACED: &str = "memory.surfaced";
    pub const STATE_UPDATE: &str = "state.update";
    pub const STATE_SUBCONSCIOUS: &str = "state.subconscious";
    pub const USER_VOICE_PROPERTIES: &str = "user.voice.properties";
    pub const AGENT_VOICE_MODULATION: &str = "agent.voice.modulation";
    pub const AUDIO_PLAYBACK_VISEMES: &str = "audio.playback.visemes";
    pub const AUDIO_PLAYBACK_PROGRESS: &str = "audio.playback.progress";
    pub const AUDIO_PLAYBACK_BACKLOG: &str = "audio.playback.backlog";
    pub const AUDIO_PLAYBACK_LIFECYCLE: &str = "audio.playback.lifecycle";
    pub const AMBIENT_NOISE_TELEMETRY: &str = "ambient.noise.telemetry";
    pub const SESSION_PRESENCE: &str = "state.presence";
}

pub const HEADER_LATENCY_META: &str = "X-Latency-Meta";
pub const HEADER_PAYLOAD_FORMAT: &str = "X-Payload-Format";
pub const PAYLOAD_FORMAT_RAW_PCM: &str = "binary/raw-pcm";

pub type JsonMap = BTreeMap<String, serde_json::Value>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LatencyHop {
    pub agent: String,
    pub subject: String,
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LatencyMetadata {
    pub start_time: f64,
    pub hops: Vec<LatencyHop>,
    pub source: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub channels: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sample_rate: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatInputMetadata {
    #[serde(default = "default_whisper_source")]
    pub source: String,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
    #[serde(default)]
    pub utterance_id: Option<String>,
}

fn default_whisper_source() -> String {
    "whisper".to_string()
}

fn default_confidence() -> f64 {
    0.9
}

impl Default for ChatInputMetadata {
    fn default() -> Self {
        Self {
            source: default_whisper_source(),
            confidence: default_confidence(),
            utterance_id: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatInput {
    pub text: String,
    #[serde(default)]
    pub utterance_id: Option<String>,
    #[serde(default)]
    pub turn_id: Option<String>,
    #[serde(default)]
    pub metadata: ChatInputMetadata,
    #[serde(default)]
    pub latency_metadata: Option<LatencyMetadata>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatOutputAffect {
    #[serde(default)]
    pub valence: f64,
    #[serde(default = "default_half")]
    pub arousal: f64,
    #[serde(default = "default_half")]
    pub dominance: f64,
    #[serde(default = "default_half")]
    pub trust: f64,
    #[serde(default = "default_attachment")]
    pub attachment: f64,
    #[serde(default = "default_neutral")]
    pub emotion: String,
    #[serde(default)]
    pub fatigue: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_distance: Option<f64>,
}

fn default_half() -> f64 {
    0.5
}

fn default_attachment() -> f64 {
    0.1
}

fn default_neutral() -> String {
    "neutral".to_string()
}

impl Default for ChatOutputAffect {
    fn default() -> Self {
        Self {
            valence: 0.0,
            arousal: 0.5,
            dominance: 0.5,
            trust: 0.5,
            attachment: 0.1,
            emotion: default_neutral(),
            fatigue: 0.0,
            user_distance: None,
        }
    }
}

// Phase 3B/3C (§18 Experiment 4): wire mirror of Python's
// `contracts.SpeechExpressionWire`, itself a mirror of the in-process
// `cognitive.expression.SpeechExpression` (Phase 3A). `#[serde(default)]`
// on every field, no `deny_unknown_fields`, matching every other wire type
// here -- a message from a producer that hasn't shipped this yet (true of
// every producer today) deserializes to `Default::default()`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SpeechExpressionWire {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub affect_label: Option<String>,
    #[serde(default)]
    pub breath: f64,
    #[serde(default)]
    pub hesitation: f64,
    #[serde(default = "default_neutral")]
    pub style: String,
    // Stage 3 fix: was `Vec<f64>` -- couldn't deserialize Codex's Phase 3A
    // `SpeechExpression.trajectory`, which preserves `generate_apra_trajectory`'s
    // native `(time_offset_ms, rate, pitch, volume)` frame tuples verbatim.
    // serde maps a Python tuple/JSON array of 4 elements straight onto this
    // Rust tuple, matching `ProsodyFrame`'s field shape (see below) without
    // introducing a second named struct for the same four values.
    #[serde(default)]
    pub trajectory: Vec<(i64, f64, f64, f64)>,
}

impl Default for SpeechExpressionWire {
    fn default() -> Self {
        Self {
            affect_label: None,
            breath: 0.0,
            hesitation: 0.0,
            style: default_neutral(),
            trajectory: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ChatOutput {
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub done: bool,
    #[serde(default)]
    pub turn_id: Option<String>,
    #[serde(default)]
    pub affect: Option<ChatOutputAffect>,
    // Optional and skipped when absent (`skip_serializing_if`) so the
    // existing `chat_output_round_trips_current_contract_shape` fixture
    // test, which asserts byte-for-byte round-trip equality against a
    // fixture with no `expression` key, keeps passing unchanged -- the
    // same pattern `user_distance` above already uses for the same reason.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expression: Option<SpeechExpressionWire>,
    // The deprecated prosody block (confidence, intensity, speaking_rate,
    // pause_bias, paralinguistic_tags) was removed. Prosody has one source:
    // `vad_to_prosody` derives it from `affect` above. Nothing read these, and
    // the Python side populated them with a different formula.
    //
    // Removal is safe both ways round: this struct has no
    // `deny_unknown_fields`, so a message from an older producer still
    // carrying them deserializes and they are ignored. Note that
    // `contracts::Prosody` has its own `pause_bias` -- a different type, not
    // affected by this.
    #[serde(default)]
    pub timestamp: f64,
    #[serde(default)]
    pub full_response: Option<String>,
    #[serde(default)]
    pub generation_error: Option<String>,
    #[serde(default)]
    pub proactive: bool,
    // P4-2: previously absent from this struct entirely -- present in the
    // Python model (`extra: "allow"`, no `deny_unknown_fields` on this side
    // either) but silently dropped on deserialization here, so brain_agent's
    // `character_offset`/`word_index` (used to build `audio.playback.progress`)
    // never reached voice-agent at all. `JsonMap` rather than a typed struct
    // because this field's meaning is caller-defined per message, the same
    // reasoning `AudioPerception::metadata` (below) already uses it for.
    #[serde(default)]
    pub metadata: JsonMap,
    #[serde(default)]
    pub latency_metadata: Option<LatencyMetadata>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SpeculativeIntent {
    #[serde(default = "default_speculative_stop")]
    pub name: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub timestamp: f64,
    #[serde(default)]
    pub utterance_id: Option<String>,
}

fn default_speculative_stop() -> String {
    "SPECULATIVE_STOP".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioPerception {
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub intent: Option<String>,
    #[serde(default = "default_conversational")]
    pub intent_type: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub snr: f64,
    #[serde(default)]
    pub paralinguistic_events: Vec<String>,
    #[serde(default)]
    pub speculative_intent: Option<SpeculativeIntent>,
    #[serde(default)]
    pub metadata: JsonMap,
    #[serde(default)]
    pub timestamp: f64,
    #[serde(default)]
    pub utterance_id: Option<String>,
}

fn default_conversational() -> String {
    "CONVERSATIONAL".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioStop {
    #[serde(default = "default_true")]
    pub interrupt: bool,
    #[serde(default)]
    pub flush: bool,
    #[serde(default)]
    pub speculative: bool,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub command_text: Option<String>,
    #[serde(default)]
    pub intent: Option<String>,
    #[serde(default = "default_voice_interruption")]
    pub intent_type: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub perception_text: Option<String>,
    #[serde(default)]
    pub utterance_id: Option<String>,
    #[serde(default)]
    pub turn_id: Option<String>,
}

fn default_true() -> bool {
    true
}

fn default_voice_interruption() -> String {
    "VOICE_INTERRUPTION".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioResume {
    #[serde(default = "default_conflict_rejected")]
    pub reason: String,
    #[serde(default)]
    pub perception_text: Option<String>,
    #[serde(default)]
    pub utterance_id: Option<String>,
    // Phase 2D (§15 item 8): mirrors `AudioStop.turn_id` above -- makes
    // `audio.resume` turn-scoped symmetrically with `audio.stop`, which was
    // the one gap (ground truth: `AudioStop` was already turn-scoped).
    #[serde(default)]
    pub turn_id: Option<String>,
}

fn default_conflict_rejected() -> String {
    "conflict_rejected".to_string()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserVoiceProperties {
    pub pitch_f0: f64,
    pub energy_rms: f64,
    // docs/FUTURE_WORK.md §1.2: `None` until the first utterance completes
    // and a real words-over-duration rate exists to publish -- a fabricated
    // default here would be indistinguishable from a real measurement to
    // every consumer. Changed on both sides of the wire together (this file
    // and backend/app/contracts.py); a one-sided change here is exactly the
    // silent-drop failure mode the char_offset bug already taught this repo.
    #[serde(default)]
    pub tempo_wpm: Option<f64>,
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProsodyFrame {
    pub time_offset_ms: u32,
    pub rate: f64,
    pub pitch: f64,
    pub volume: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentVoiceModulation {
    pub trajectory: Vec<ProsodyFrame>,
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PlaybackVisemes {
    pub target_level: f64,
    pub viseme_id: String,
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioPlaybackProgress {
    pub utterance_id: String,
    pub character_offset: u64,
    pub word_index: u64,
    pub completed: bool,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PlaybackLifecycleState {
    Started,
    Playing,
    Completed,
    Interrupted,
    Failed,
}

impl PlaybackLifecycleState {
    fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Interrupted | Self::Failed)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioPlaybackLifecycle {
    pub utterance_id: String,
    pub turn_id: String,
    pub seq: u64,
    pub state: PlaybackLifecycleState,
    pub words_played: u64,
    pub words_streamed: u64,
    pub heard_offset: u64,
    pub streamed_offset: u64,
    /// INTERRUPTED by a self-correction flush (DR-029): a retry of the same
    /// turn follows, so this is not the reply's end.
    #[serde(default)]
    pub flushed: bool,
    pub timestamp: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AudioStreamTrailer {
    pub kind: String,
    pub utterance_id: String,
    pub turn_id: String,
    #[serde(default)]
    pub failed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LifecycleApplyResult {
    Applied,
    Duplicate,
    OutOfOrder,
    ProtocolError,
}

/// Mirrors `app.contracts.PlaybackLifecycleTracker`: any event may be the
/// first one seen (the topic is best effort), `seq` orders the rest, and
/// the oldest utterance is dropped past `max_entries`.
#[derive(Debug)]
pub struct PlaybackLifecycleTracker {
    events: BTreeMap<(String, String), AudioPlaybackLifecycle>,
    order: std::collections::VecDeque<(String, String)>,
    max_entries: usize,
    protocol_errors: u64,
}

impl Default for PlaybackLifecycleTracker {
    fn default() -> Self {
        Self::with_capacity(1024)
    }
}

impl PlaybackLifecycleTracker {
    pub fn with_capacity(max_entries: usize) -> Self {
        Self {
            events: BTreeMap::new(),
            order: std::collections::VecDeque::new(),
            max_entries,
            protocol_errors: 0,
        }
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    pub fn protocol_errors(&self) -> u64 {
        self.protocol_errors
    }

    pub fn get(&self, utterance_id: &str, turn_id: &str) -> Option<&AudioPlaybackLifecycle> {
        self.events
            .get(&(utterance_id.to_string(), turn_id.to_string()))
    }

    pub fn apply(&mut self, event: AudioPlaybackLifecycle) -> LifecycleApplyResult {
        if event.words_played > event.words_streamed || event.heard_offset > event.streamed_offset {
            self.protocol_errors += 1;
            return LifecycleApplyResult::ProtocolError;
        }
        let key = (event.utterance_id.clone(), event.turn_id.clone());
        let Some(previous) = self.events.get(&key) else {
            self.order.push_back(key.clone());
            self.events.insert(key, event);
            while self.events.len() > self.max_entries {
                match self.order.pop_front() {
                    Some(oldest) => {
                        self.events.remove(&oldest);
                    }
                    None => break,
                }
            }
            return LifecycleApplyResult::Applied;
        };

        if previous.state.is_terminal() {
            if event.state == previous.state {
                return LifecycleApplyResult::Duplicate;
            }
            if event.state.is_terminal() {
                self.protocol_errors += 1;
                return LifecycleApplyResult::ProtocolError;
            }
            return LifecycleApplyResult::OutOfOrder;
        }
        if event.seq < previous.seq {
            return LifecycleApplyResult::OutOfOrder;
        }
        if event.seq == previous.seq {
            if event.state == previous.state {
                return LifecycleApplyResult::Duplicate;
            }
            // One seq, two states: the producer broke its own ordering.
            self.protocol_errors += 1;
            return LifecycleApplyResult::ProtocolError;
        }
        if event.state == PlaybackLifecycleState::Started {
            return LifecycleApplyResult::OutOfOrder;
        }
        self.events.insert(key, event);
        LifecycleApplyResult::Applied
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AmbientNoiseTelemetry {
    pub rms_energy: f64,
    pub noise_floor_db: f64,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Prosody {
    pub rate: f64,
    pub pitch: f64,
    pub volume: f64,
    pub pause_bias: f64,
}

pub fn vad_to_prosody(affect: Option<&ChatOutputAffect>) -> Prosody {
    let affect = affect.cloned().unwrap_or_default();

    // Fatigue dynamically slows down the rate and reduces pitch
    let fatigue_slow = 0.25 * affect.fatigue;
    let fatigue_pitch_drop = 0.1 * affect.fatigue;

    // Distance adaptation
    let user_distance = affect.user_distance.unwrap_or(1.0);
    let (dist_vol_mod, dist_pitch_mod) = if user_distance < 0.6 {
        (-0.15, -0.05) // close range (whisper)
    } else if user_distance > 1.5 {
        (0.2, 0.1) // far range (loud/calling out)
    } else {
        (0.0, 0.0) // baseline
    };

    // Continuous formulas for prosody trajectory calculation
    // Sr = 1.0 + tanh(0.20 * arousal - 0.10 * valence - fatigue_slow)
    let rate_input = (0.20 * affect.arousal) - (0.10 * affect.valence) - fatigue_slow;
    let rate = 1.0 + rate_input.tanh();

    // Pm = 1.0 + tanh(0.05 * valence + 0.15 * arousal - 0.10 * dominance - fatigue_pitch_drop + dist_pitch_mod)
    let pitch_input = (0.05 * affect.valence) + (0.15 * affect.arousal)
        - (0.10 * affect.dominance)
        - fatigue_pitch_drop
        + dist_pitch_mod;
    let pitch = 1.0 + pitch_input.tanh();

    let volume = 0.4 + affect.dominance * 0.6 + dist_vol_mod;

    let v = 1.0 - affect.arousal;
    let pause_bias = v.clamp(0.0, 1.0);

    Prosody {
        rate: clamp_round(rate, 0.6, 1.8),
        pitch: clamp_round(pitch, 0.5, 2.0),
        volume: clamp_round(volume, 0.1, 1.0),
        pause_bias,
    }
}

fn clamp_round(value: f64, min: f64, max: f64) -> f64 {
    let clamped = value.max(min).min(max);
    (clamped * 100.0).round() / 100.0
}

pub fn silence_pcm(ms: u32, sample_rate: u32) -> Vec<u8> {
    let samples = ((ms as u64).saturating_mul(sample_rate as u64) + 999) / 1000;
    let bytes = samples.saturating_mul(2);
    vec![0; bytes as usize]
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::{prop_assert, prop_assert_eq};

    #[test]
    fn chat_output_round_trips_current_contract_shape() {
        let fixture = include_str!("../fixtures/chat_output_chunk.json");
        let parsed: ChatOutput = serde_json::from_str(fixture).unwrap();

        assert_eq!(
            parsed.content.as_deref(),
            Some("Hey there<pause=20ms>friend")
        );
        assert_eq!(parsed.turn_id.as_deref(), Some("turn-1"));
        assert_eq!(parsed.affect.as_ref().unwrap().emotion, "happy");

        let serialized = serde_json::to_value(parsed).unwrap();
        let expected: serde_json::Value = serde_json::from_str(fixture).unwrap();
        assert_eq!(serialized, expected);
    }

    // Phase 3B/3C: the shared fixture has no `expression` key at all (no
    // producer ships it yet), so this is the actual regression this test
    // guards -- a required field here would break every existing producer
    // on the old shape, which the field being `Option`-with-`#[serde(default)]`
    // is supposed to prevent.
    #[test]
    fn chat_output_expression_defaults_to_none_when_absent() {
        let fixture = include_str!("../fixtures/chat_output_chunk.json");
        let parsed: ChatOutput = serde_json::from_str(fixture).unwrap();

        assert!(parsed.expression.is_none());
    }

    #[test]
    fn chat_output_expression_round_trips_when_present() {
        let mut parsed: ChatOutput =
            serde_json::from_str(include_str!("../fixtures/chat_output_chunk.json")).unwrap();
        parsed.expression = Some(SpeechExpressionWire {
            affect_label: Some("warm".to_string()),
            breath: 0.4,
            hesitation: 0.6,
            style: "soft".to_string(),
            trajectory: vec![(0, 0.95, 1.1, 0.1), (17, 0.96, 1.1, 0.11)],
        });

        let serialized = serde_json::to_value(&parsed).unwrap();
        let round_tripped: ChatOutput = serde_json::from_value(serialized).unwrap();

        assert_eq!(round_tripped.expression, parsed.expression);
    }

    // Stage 3 (Blocker 1): the actual shape crossing the Python/Rust wire once
    // a producer exists -- a JSON array of 4-tuples, exactly what Codex's
    // `cognitive_rust.generate_apra_trajectory` / `SpeechExpression.trajectory`
    // produces (`(time_offset_ms, rate, pitch, volume)`, 60 frames at 50ms
    // spacing in production). This is the cross-boundary regression Codex's
    // Stage 2 review asked for: a payload shaped like the real producer's
    // output, not just a same-language struct literal.
    #[test]
    fn speech_expression_trajectory_deserializes_apra_frame_json() {
        let json = r#"{
            "affect_label": "calm",
            "breath": 0.0,
            "hesitation": 0.0,
            "style": "natural",
            "trajectory": [[0, 0.95, 1.1, 0.1], [50, 0.96, 1.08, 0.12], [100, 0.97, 1.05, 0.13]]
        }"#;

        let parsed: SpeechExpressionWire = serde_json::from_str(json).unwrap();

        assert_eq!(
            parsed.trajectory,
            vec![
                (0, 0.95, 1.1, 0.1),
                (50, 0.96, 1.08, 0.12),
                (100, 0.97, 1.05, 0.13)
            ]
        );

        let round_tripped: SpeechExpressionWire =
            serde_json::from_value(serde_json::to_value(&parsed).unwrap()).unwrap();
        assert_eq!(round_tripped, parsed);
    }

    #[test]
    fn speech_expression_wire_defaults_match_python_contract() {
        // Mirrors `contracts.SpeechExpressionWire`'s Python-side defaults
        // (affect_label=None, breath=0.0, hesitation=0.0, style="neutral",
        // trajectory=[]) exactly, so a message missing every field still
        // deserializes to the same shape either side would construct.
        let parsed: SpeechExpressionWire = serde_json::from_str("{}").unwrap();
        assert_eq!(parsed, SpeechExpressionWire::default());
        assert_eq!(parsed.style, "neutral");
        assert!(parsed.trajectory.is_empty());
    }

    #[test]
    fn speculative_audio_stop_round_trips_current_contract_shape() {
        let fixture = include_str!("../fixtures/audio_stop_speculative.json");
        let parsed: AudioStop = serde_json::from_str(fixture).unwrap();

        assert!(parsed.interrupt);
        assert!(parsed.speculative);
        assert!(!parsed.flush);
        assert_eq!(parsed.intent.as_deref(), Some("SPECULATIVE_STOP"));

        let serialized = serde_json::to_value(parsed).unwrap();
        let expected: serde_json::Value = serde_json::from_str(fixture).unwrap();
        assert_eq!(serialized, expected);
    }

    #[test]
    fn lifecycle_fixture_matches_the_rust_contract() {
        let fixture = include_str!("../fixtures/audio_playback_lifecycle_started.json");
        let parsed: AudioPlaybackLifecycle = serde_json::from_str(fixture).unwrap();
        assert_eq!(parsed.utterance_id, "utt-1");
        assert_eq!(parsed.turn_id, "turn-1");
        assert_eq!(parsed.seq, 0);
        assert_eq!(parsed.state, PlaybackLifecycleState::Started);
        assert_eq!(parsed.words_played, 0);
        assert_eq!(parsed.words_streamed, 4);
    }

    #[test]
    fn audio_stream_trailer_fixture_matches_the_rust_contract() {
        let fixture = include_str!("../fixtures/audio_stream_trailer.json");
        let parsed: AudioStreamTrailer = serde_json::from_str(fixture).unwrap();
        assert_eq!(parsed.kind, "END_OF_STREAM");
        assert_eq!(parsed.utterance_id, "utt-1");
        assert_eq!(parsed.turn_id, "turn-1");
        assert!(!parsed.failed);
    }

    #[test]
    fn source_failure_before_first_frame_is_terminal() {
        let mut tracker = PlaybackLifecycleTracker::default();
        let failed = AudioPlaybackLifecycle {
            utterance_id: "utt-1".into(),
            turn_id: "turn-1".into(),
            seq: 0,
            state: PlaybackLifecycleState::Failed,
            words_played: 0,
            words_streamed: 1,
            heard_offset: 0,
            streamed_offset: 7,
            flushed: false,
            timestamp: 0.0,
        };
        assert_eq!(tracker.apply(failed.clone()), LifecycleApplyResult::Applied);
        let mut completed = failed;
        completed.seq = 1;
        completed.state = PlaybackLifecycleState::Completed;
        assert_eq!(
            tracker.apply(completed),
            LifecycleApplyResult::ProtocolError
        );
    }

    fn lifecycle(
        utterance: &str,
        seq: u64,
        state: PlaybackLifecycleState,
    ) -> AudioPlaybackLifecycle {
        AudioPlaybackLifecycle {
            utterance_id: utterance.into(),
            turn_id: "turn-1".into(),
            seq,
            state,
            words_played: 0,
            words_streamed: 0,
            heard_offset: 0,
            streamed_offset: 0,
            flushed: false,
            timestamp: 0.0,
        }
    }

    #[test]
    fn a_lost_started_does_not_turn_the_terminal_away() {
        // Best-effort topic: STARTED and PLAYING may never arrive.
        let mut tracker = PlaybackLifecycleTracker::default();
        let completed = lifecycle("utt-1", 5, PlaybackLifecycleState::Completed);
        assert_eq!(tracker.apply(completed), LifecycleApplyResult::Applied);
        let late_started = lifecycle("utt-1", 0, PlaybackLifecycleState::Started);
        assert_eq!(
            tracker.apply(late_started),
            LifecycleApplyResult::OutOfOrder
        );
    }

    #[test]
    fn one_seq_with_two_states_is_a_protocol_error() {
        let mut tracker = PlaybackLifecycleTracker::default();
        tracker.apply(lifecycle("utt-1", 3, PlaybackLifecycleState::Playing));
        assert_eq!(
            tracker.apply(lifecycle("utt-1", 3, PlaybackLifecycleState::Playing)),
            LifecycleApplyResult::Duplicate
        );
        assert_eq!(
            tracker.apply(lifecycle("utt-1", 3, PlaybackLifecycleState::Completed)),
            LifecycleApplyResult::ProtocolError
        );
        assert_eq!(tracker.protocol_errors(), 1);
    }

    #[test]
    fn the_tracker_keeps_at_most_max_entries_utterances() {
        let mut tracker = PlaybackLifecycleTracker::with_capacity(3);
        for n in 0..10 {
            tracker.apply(lifecycle(
                &format!("utt-{n}"),
                0,
                PlaybackLifecycleState::Started,
            ));
        }
        assert_eq!(tracker.len(), 3);
        assert!(tracker.get("utt-0", "turn-1").is_none());
        assert!(tracker.get("utt-9", "turn-1").is_some());
    }

    #[test]
    fn flushed_defaults_to_false_on_the_wire() {
        let fixture = include_str!("../fixtures/audio_playback_lifecycle_started.json");
        let parsed: AudioPlaybackLifecycle = serde_json::from_str(fixture).unwrap();
        assert!(!parsed.flushed);
    }

    proptest::proptest! {
        #[test]
        fn lifecycle_state_machine_keeps_one_terminal_and_ignores_reordering(
            states in proptest::collection::vec(0u8..5, 1..80),
            seqs in proptest::collection::vec(0u64..80, 1..80),
        ) {
            let mut tracker = PlaybackLifecycleTracker::default();
            let mut terminal = None;
            let count = states.len().min(seqs.len());
            for index in 0..count {
                let state = match states[index] {
                    0 => PlaybackLifecycleState::Started,
                    1 => PlaybackLifecycleState::Playing,
                    2 => PlaybackLifecycleState::Completed,
                    3 => PlaybackLifecycleState::Interrupted,
                    _ => PlaybackLifecycleState::Failed,
                };
                let event = AudioPlaybackLifecycle {
                    utterance_id: "utt-1".into(),
                    turn_id: "turn-1".into(),
                    seq: seqs[index],
                    state,
                    words_played: 0,
                    words_streamed: 0,
                    heard_offset: 0,
                    streamed_offset: 0,
                    flushed: false,
                    timestamp: index as f64,
                };
                let result = tracker.apply(event);
                if result == LifecycleApplyResult::Applied && state.is_terminal() {
                    prop_assert!(terminal.is_none());
                    terminal = Some(state);
                }
                if let Some(terminal_state) = terminal {
                    prop_assert_eq!(tracker.get("utt-1", "turn-1").unwrap().state, terminal_state);
                }
            }
        }
    }

    #[test]
    fn silence_uses_configured_sample_rate() {
        assert_eq!(silence_pcm(10, 16_000).len(), 320);
        assert_eq!(silence_pcm(10, 32_000).len(), 640);
    }

    #[test]
    fn prosody_pitch_respects_negative_valence_range() {
        let negative = ChatOutputAffect {
            valence: -1.0,
            ..Default::default()
        };
        let positive = ChatOutputAffect {
            valence: 1.0,
            ..Default::default()
        };

        assert!(vad_to_prosody(Some(&negative)).pitch < vad_to_prosody(Some(&positive)).pitch);
    }

    #[test]
    fn prosody_responds_to_fatigue() {
        let fresh = ChatOutputAffect {
            fatigue: 0.0,
            ..Default::default()
        };
        let tired = ChatOutputAffect {
            fatigue: 0.8,
            ..Default::default()
        };

        let fresh_prosody = vad_to_prosody(Some(&fresh));
        let tired_prosody = vad_to_prosody(Some(&tired));

        assert!(tired_prosody.rate < fresh_prosody.rate);
        assert!(tired_prosody.pitch < fresh_prosody.pitch);
    }

    #[test]
    fn prosody_adapts_to_user_distance() {
        let close = ChatOutputAffect {
            user_distance: Some(0.4),
            ..Default::default()
        };
        let far = ChatOutputAffect {
            user_distance: Some(2.0),
            ..Default::default()
        };

        let close_prosody = vad_to_prosody(Some(&close));
        let far_prosody = vad_to_prosody(Some(&far));

        assert!(close_prosody.volume < far_prosody.volume);
        assert!(close_prosody.pitch < far_prosody.pitch);
    }
}
