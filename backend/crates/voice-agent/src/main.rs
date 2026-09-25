use anyhow::{Context, Result};
use async_nats::HeaderMap;
use bytes::Bytes;
use contracts::{
    topics, vad_to_prosody, AmbientNoiseTelemetry, AudioStreamTrailer, ChatOutput, PlaybackVisemes,
    HEADER_LATENCY_META, HEADER_PAYLOAD_FORMAT, PAYLOAD_FORMAT_RAW_PCM,
};
use futures_util::StreamExt;
use regex::Regex;
use reqwest::Client;
use serde_json::json;
use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{error, info, warn};

/// Mirrors the clamps `contracts::vad_to_prosody` already applies. Repeated here
/// because `dynamic_prosody` can be overridden from the mesh and must not be
/// trusted to be in range — a zero or negative rate would divide by zero below.
const MIN_RATE: f32 = 0.6;
const MAX_RATE: f32 = 1.8;
const MIN_PITCH: f32 = 0.5;
const MAX_PITCH: f32 = 2.0;
const MIN_VOLUME: f32 = 0.1;
const MAX_VOLUME: f32 = 1.0;

/// Force mesh-supplied prosody into the ranges the audio path assumes.
///
/// `vad_to_prosody` clamps its own output, but a `dynamic_prosody` override
/// arrives over `agent.voice.modulation` and is only as sane as its publisher.
/// Clamping at the point of *selection* rather than inside `synthesize` is what
/// makes that safe: local synthesis was the only consumer that re-clamped, so
/// remote synthesis, hesitation pitch and vocalization gain all took whatever the
/// mesh said — a negative volume inverts the waveform, and a zero rate divides by
/// zero in the length-scale computation.
/// The turn currently being spoken, shared between the chat.output loop that
/// tracks it and the audio.stop task that must respect it.
type ActiveTurn = std::sync::Arc<std::sync::Mutex<Option<String>>>;

/// Whether a turn-scoped mesh signal (`AudioStop` or, since Phase 2D,
/// `AudioResume`) should act on the turn currently being spoken.
///
/// `AudioStop.turn_id` was previously ignored entirely, so a stop that had been
/// delayed in the mesh — or one emitted for a turn that has since finished —
/// aborted whatever the agent had *started saying next*. The user saw the agent
/// cut itself off mid-sentence for an interruption aimed at a reply that was
/// already over. `AudioResume` had the same gap in the other direction: a
/// resume delayed in the mesh could restore volume for a turn that had since
/// been genuinely stopped. Same helper both directions -- the scoping rule
/// ("does this signal name a turn, and if so does it match what's active") is
/// identical for either subject.
///
/// A signal naming no turn stays unscoped and is always honoured: barge-in from
/// the STT path is not always able to name the turn it is interrupting, and
/// silently ignoring those would make interruption (or resume) stop working
/// altogether. Only an *explicit* mismatch is rejected.
fn mesh_signal_applies_to_active_turn(active: &ActiveTurn, signal_turn: Option<&str>) -> bool {
    let Some(signal_turn) = signal_turn else {
        return true;
    };
    match active.lock() {
        // Nothing is speaking, so there is no current turn to protect; honouring
        // the signal is harmless (a new turn clears the abort flag when it starts).
        Ok(guard) => guard.as_deref().is_none_or(|active| active == signal_turn),
        Err(_) => true,
    }
}

fn stop_aborts_generation(stop: &contracts::AudioStop) -> bool {
    !stop.speculative && !stop.flush
}

/// P2-1, opt-in: connects with a username/password only when both are
/// given, mirroring `BaseAgent.connect` (Python) so both halves of the mesh
/// honour the same opt-in credential -- see nats-accounts.conf's own header
/// for how an operator turns this on. With neither given (the default),
/// this is `async_nats::connect(url)`, unchanged from before this existed.
/// Takes the credentials as parameters rather than reading
/// `NATS_USER`/`NATS_PASSWORD` internally so tests can exercise both
/// branches without mutating this process's real environment (`cargo test`
/// runs tests in parallel by default, and threads share one environment).
async fn connect_nats(
    url: &str,
    user: Option<String>,
    password: Option<String>,
) -> std::result::Result<async_nats::Client, async_nats::ConnectError> {
    match (user, password) {
        (Some(user), Some(password)) => {
            async_nats::ConnectOptions::new()
                .user_and_password(user, password)
                .connect(url)
                .await
        }
        _ => async_nats::connect(url).await,
    }
}

fn clamp_prosody(mut prosody: contracts::Prosody) -> contracts::Prosody {
    prosody.rate = prosody.rate.clamp(MIN_RATE as f64, MAX_RATE as f64);
    prosody.pitch = prosody.pitch.clamp(MIN_PITCH as f64, MAX_PITCH as f64);
    prosody.volume = prosody.volume.clamp(MIN_VOLUME as f64, MAX_VOLUME as f64);
    prosody.pause_bias = prosody.pause_bias.clamp(0.0, 1.0);
    prosody
}

/// P3-13: `generate_apra_trajectory` (cognitive-rust) models one breath
/// group's ~3s prosodic arc as 60 frames spaced 50ms apart -- onset
/// breathing dampening under 200ms, a steady middle, tail dampening past
/// 2700ms, volume fade-in/out at the very ends. The consumer used to
/// collapse all 60 frames to a single averaged (rate, pitch, volume) and
/// apply that same static value to every chat.output chunk of the whole
/// response, discarding the arc entirely. This holds the trajectory plus
/// when it arrived, so each chunk can instead sample the frame nearest how
/// long *that trajectory* has been playing -- prosody genuinely drifts
/// chunk to chunk as time passes, and a new trajectory (published on every
/// affect update, `surfacing_agent.py::_on_agent_state`) simply restarts
/// the arc from its own arrival.
struct ProsodyTrajectory {
    received_at: std::time::Instant,
    frames: Vec<contracts::ProsodyFrame>,
}

impl ProsodyTrajectory {
    /// The prosody for right now, drawn from the frame nearest how long ago
    /// this trajectory was received. Once elapsed time exceeds the
    /// trajectory's own ~3s span, the nearest frame is simply its last one
    /// (the modeled steady-state tail) -- no extra clamping needed, nearest-
    /// frame search finds that on its own.
    fn prosody_now(&self) -> Option<contracts::Prosody> {
        let elapsed_ms = self.received_at.elapsed().as_millis() as f64;
        let nearest = self.frames.iter().min_by(|a, b| {
            let da = (a.time_offset_ms as f64 - elapsed_ms).abs();
            let db = (b.time_offset_ms as f64 - elapsed_ms).abs();
            da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
        })?;
        Some(contracts::Prosody {
            rate: nearest.rate,
            pitch: nearest.pitch,
            volume: nearest.volume,
            pause_bias: 1.0,
        })
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
struct VisionDescriptionMsg {
    user_distance: Option<f64>,
}

struct ReverbFilter {
    buffer: Vec<f32>,
    index: usize,
    gain: f32,
    pending_byte: Option<u8>,
}

impl ReverbFilter {
    fn new(delay_samples: usize, gain: f32) -> Self {
        Self {
            buffer: vec![0.0; delay_samples],
            index: 0,
            gain,
            pending_byte: None,
        }
    }

    fn process(&mut self, bytes: &[u8], wet_gain: f32) -> Vec<u8> {
        let mut framed =
            Vec::with_capacity(bytes.len() + if self.pending_byte.is_some() { 1 } else { 0 });
        if let Some(byte) = self.pending_byte.take() {
            framed.push(byte);
        }
        framed.extend_from_slice(bytes);
        if framed.len() % 2 != 0 {
            self.pending_byte = framed.pop();
        }

        let mut samples = framed
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        // Bucket 2 (VOICE_REMEDIATION_PLAN.md): headroom for the wet path. With the
        // feedback bug below fixed, the worst case is the input and its single delayed
        // echo both at full scale and in phase -- `1 + gain` times full scale. Scaling
        // by its reciprocal guarantees the wet signal itself cannot clip, regardless of
        // input content, rather than relying on `clamp` to hard-limit an already
        // out-of-range value into audible distortion.
        let headroom = 1.0 / (1.0 + self.gain.abs());

        for sample in samples.iter_mut() {
            let input = *sample as f32;
            let delayed = self.buffer[self.index];
            // Bucket 2: store the INPUT in the delay line, not the previous output.
            // Writing `output` here made this `y[n] = x[n] + gain*y[n-D]` -- true
            // feedback, whose steady-state gain is `1/(1-gain)` (2.0x at gain=0.5),
            // which is what was driving `clamp` into a hard clipper on normalised TTS
            // output. Writing `input` makes it a simple echo, `y[n] = x[n] + gain*x[n-D]`,
            // whose gain is bounded (`1+gain`, 1.5x at gain=0.5) and cannot run away.
            self.buffer[self.index] = input;
            self.index = (self.index + 1) % self.buffer.len();

            let echoed = (input + self.gain * delayed) * headroom;
            let blended = (1.0 - wet_gain) * input + wet_gain * echoed;
            *sample = blended.clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        }

        let mut output_bytes = Vec::with_capacity(samples.len() * 2);
        for sample in samples {
            output_bytes.extend_from_slice(&sample.to_le_bytes());
        }
        output_bytes
    }

    /// Bucket 2 (VOICE_REMEDIATION_PLAN.md): clears the delay line and playback
    /// position. Must be called at utterance boundaries (`event.done`), not per-chunk
    /// (that would drop the previous chunk's echo tail at every boundary -- see the
    /// comment where this filter is constructed) -- but it must be called *somewhere*,
    /// since before this the filter was constructed once per process and never reset
    /// at all, so a reverb tail could bleed from one utterance into a completely
    /// unrelated later one for the life of the process.
    fn reset(&mut self) {
        self.buffer.iter_mut().for_each(|s| *s = 0.0);
        self.index = 0;
        // Reviewer finding: a trailing odd byte held from the previous
        // utterance's PCM must not combine with the next utterance's first
        // byte -- the sibling PcmSampleFramer's own clear_history()
        // already clears this same kind of state for the same reason.
        self.pending_byte = None;
    }
}

/// Bucket 2: the distance gate previously lived only at the one `TemporalPart::Text`
/// call site (the main synthesis path) -- the filler/vocalization/failure paths each
/// hardcoded `0.1` wet regardless of distance, so a reverb tail played at the start of
/// every turn (the filler fires almost every turn -- see Bucket 3) even in normal
/// close-range conversation, where the gate says reverb should be fully dry. Same
/// distance -> wet_gain mapping now shared by every call site instead of duplicated.
fn reverb_wet_gain_for_distance(distance: f64) -> f32 {
    const REVERB_DRY_LIMIT: f64 = 2.5;
    const REVERB_WET_LIMIT: f64 = 3.5;
    if distance <= REVERB_DRY_LIMIT {
        0.0
    } else if distance >= REVERB_WET_LIMIT {
        1.0
    } else {
        ((distance - REVERB_DRY_LIMIT) / (REVERB_WET_LIMIT - REVERB_DRY_LIMIT)) as f32
    }
}

/// Preserves 16-bit PCM sample boundaries across streamed byte chunks.
/// Holds at most one trailing byte; completed samples pass through unchanged.
/// Reset on interruption so the next utterance cannot inherit a partial sample.
struct PcmSampleFramer {
    pending_byte: Option<u8>,
}

impl PcmSampleFramer {
    fn new() -> Self {
        Self { pending_byte: None }
    }

    fn clear_history(&mut self) {
        self.pending_byte = None;
    }

    fn process(&mut self, bytes: &[u8]) -> Vec<u8> {
        let mut framed =
            Vec::with_capacity(bytes.len() + if self.pending_byte.is_some() { 1 } else { 0 });
        if let Some(byte) = self.pending_byte.take() {
            framed.push(byte);
        }
        framed.extend_from_slice(bytes);
        if framed.len() % 2 != 0 {
            self.pending_byte = framed.pop();
        }
        framed
    }
}

#[derive(Debug, Clone)]
struct VoiceConfig {
    nats_url: String,
    sovits_url: String,
    emotion_refs: EmotionRefSet,
    tts_language: String,
    sample_rate: u32,
}

impl VoiceConfig {
    fn from_env() -> Self {
        Self {
            nats_url: env_or("NATS_URL", "nats://127.0.0.1:4222"),
            sovits_url: env_or("SOVITS_URL", "http://127.0.0.1:9871"),
            emotion_refs: EmotionRefSet::from_env(),
            tts_language: env_or("TTS_LANGUAGE", "en"),
            // A zero rate parses fine but sizes the reverb delay buffer to zero,
            // which panics on the first index; it would also make every duration
            // computation divide by zero. Treat it like any other unparseable value.
            sample_rate: env_or("SAMPLE_RATE", "32000")
                .parse::<u32>()
                .ok()
                .filter(|rate| *rate > 0)
                .unwrap_or(32_000),
        }
    }
}

fn env_or(name: &str, fallback: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| fallback.to_string())
}

/// One reference-audio clip GPT-SoVITS conditions delivery on: the audio path
/// (read by the SoVITS *server*, not this process — see `EmotionRefSet::resolve`)
/// plus the transcript of that clip, which the API requires alongside it.
///
/// This is not the cloned voice's identity — that is permanently baked into the
/// GPT/SoVITS weights loaded once at server startup (`CUSTOM_GPT_PATH`/
/// `CUSTOM_SOVITS_PATH`). A `RefClip` only steers *delivery* for one utterance:
/// pacing, emphasis, emotional register. Sending a different one per turn is
/// not re-cloning — the identity underneath never changes.
#[derive(Debug, Clone, PartialEq, Eq)]
struct RefClip {
    audio_path: String,
    text: String,
}

/// The five delivery registers a turn can be spoken in. `Neutral` is always
/// configured (it is today's `REF_AUDIO_PATH`/`REF_TEXT`, kept as the
/// unconditional default); the other four are optional overrides that fall
/// back to `Neutral` when unset, so an unconfigured deployment behaves exactly
/// as it did before this feature existed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum EmotionBucket {
    Calm,
    Warm,
    Concerned,
    Excited,
    Neutral,
}

#[derive(Debug, Clone)]
struct EmotionRefSet {
    neutral: RefClip,
    calm: Option<RefClip>,
    warm: Option<RefClip>,
    concerned: Option<RefClip>,
    excited: Option<RefClip>,
}

impl EmotionRefSet {
    fn from_env() -> Self {
        Self {
            neutral: RefClip {
                audio_path: env_or("REF_AUDIO_PATH", "output/sample_en_gold.wav"),
                text: env_or(
                    "REF_TEXT",
                    "At the end of the exam, the program shows the performance summary.",
                ),
            },
            calm: optional_ref_clip("CALM"),
            warm: optional_ref_clip("WARM"),
            concerned: optional_ref_clip("CONCERNED"),
            excited: optional_ref_clip("EXCITED"),
        }
    }

    /// The clip to send for this bucket. Falls back to `neutral` whenever the
    /// bucket has no override configured — deliberately silent, not a warning
    /// on every turn: an unconfigured deployment is the expected starting
    /// state, not a misconfiguration.
    fn resolve(&self, bucket: EmotionBucket) -> &RefClip {
        match bucket {
            EmotionBucket::Neutral => &self.neutral,
            EmotionBucket::Calm => self.calm.as_ref().unwrap_or(&self.neutral),
            EmotionBucket::Warm => self.warm.as_ref().unwrap_or(&self.neutral),
            EmotionBucket::Concerned => self.concerned.as_ref().unwrap_or(&self.neutral),
            EmotionBucket::Excited => self.excited.as_ref().unwrap_or(&self.neutral),
        }
    }
}

/// Reads `REF_AUDIO_PATH_{suffix}` / `REF_TEXT_{suffix}`. Both must be present
/// and non-empty or the bucket is treated as entirely unconfigured — pairing a
/// real audio path with a missing/wrong transcript would send GPT-SoVITS a
/// prompt that does not match its own reference audio, which is worse than
/// falling back to neutral.
fn optional_ref_clip(bucket_env_suffix: &str) -> Option<RefClip> {
    let audio_path = std::env::var(format!("REF_AUDIO_PATH_{bucket_env_suffix}"))
        .ok()
        .filter(|s| !s.trim().is_empty())?;
    let text = std::env::var(format!("REF_TEXT_{bucket_env_suffix}"))
        .ok()
        .filter(|s| !s.trim().is_empty())?;
    Some(RefClip { audio_path, text })
}

/// Startup diagnostic only: GPT-SoVITS resolves `ref_audio_path` itself, in
/// its own container's filesystem (see the `RefClip` doc comment above), so
/// this process never actually needs the file to do its job. Before this
/// check existed, a missing clip produced no symptom anywhere in this
/// process's own logs -- just a healthcheck failing elsewhere in the
/// dependency chain with nothing pointing back at the cause. Names the
/// missing path and the env var that set it, so that investigation is one
/// log line instead of tracing the whole compose dependency graph.
fn reference_clip_missing(path: &str) -> bool {
    std::fs::metadata(path).is_err()
}

fn take_stream_failure(failed_turns: &mut HashSet<String>, turn_id: Option<&str>) -> bool {
    turn_id.is_some_and(|turn_id| failed_turns.remove(turn_id))
}

fn warn_if_reference_clip_missing(env_var: &str, clip: &RefClip) {
    if reference_clip_missing(&clip.audio_path) {
        warn!(
            env_var,
            path = %clip.audio_path,
            "reference clip not visible from voice-agent's own filesystem -- \
             GPT-SoVITS may still resolve it in its own container, but if \
             synthesis fails, start here"
        );
    }
}

/// Maps the affect already computed for this turn onto a delivery register.
///
/// Valence and arousal are the two axes GPT-SoVITS reference clips actually
/// vary along — a different clip changes *how* a line is delivered, which is
/// exactly what the pleasure/arousal circumplex describes. Trust, attachment
/// and dominance already shape speed/pitch/volume via `vad_to_prosody`; they
/// do not additionally pick which clip is speaking.
///
/// These thresholds are a first-pass default, not a validated mapping — no
/// published study covers GPT-SoVITS multi-clip identity drift by affect
/// distance. Retune by listening once real emotion-tagged clips exist.
///
/// Phase 3C (§18 Experiment 4): `expression` is an additive, higher-priority
/// lookup on top of this -- a Phase 3A producer that already named the
/// register (`affect_label`/`style`) shouldn't be second-guessed by
/// re-deriving it from raw valence/arousal, the same "one computation, not
/// two disagreeing ones" reasoning `derive_speech_expression` itself
/// documents. `None` (true for every producer today, since 3A hasn't
/// shipped) falls straight through to the existing threshold logic
/// unchanged.
fn select_emotion_bucket(
    affect: Option<&contracts::ChatOutputAffect>,
    expression: Option<&contracts::SpeechExpressionWire>,
) -> EmotionBucket {
    if let Some(bucket) = expression.and_then(emotion_bucket_from_expression) {
        return bucket;
    }

    const VALENCE_DEADBAND: f64 = 0.15;
    const CALM_AROUSAL_CEILING: f64 = 0.40;
    const EXCITED_AROUSAL_FLOOR: f64 = 0.60;
    const CONCERNED_AROUSAL_FLOOR: f64 = 0.55;

    let Some(affect) = affect else {
        return EmotionBucket::Neutral;
    };
    let valence = affect.valence;
    let arousal = affect.arousal;

    if valence > VALENCE_DEADBAND && arousal >= EXCITED_AROUSAL_FLOOR {
        EmotionBucket::Excited
    } else if valence > VALENCE_DEADBAND {
        EmotionBucket::Warm
    } else if valence < -VALENCE_DEADBAND && arousal >= CONCERNED_AROUSAL_FLOOR {
        EmotionBucket::Concerned
    } else if valence.abs() <= VALENCE_DEADBAND && arousal < CALM_AROUSAL_CEILING {
        EmotionBucket::Calm
    } else {
        EmotionBucket::Neutral
    }
}

/// Matches `expression.affect_label` against this module's own
/// `EmotionBucket` variant names, case-insensitively.
///
/// Deliberately does *not* also fall back to `expression.style`, even
/// though both are named in the Phase 3 plan as inputs here: `style`
/// defaults to `"neutral"` on the Python/Rust wire types (not `Optional`),
/// so an unpopulated `SpeechExpressionWire` -- indistinguishable on the
/// wire from a genuinely-computed "neutral" style -- would otherwise force
/// `EmotionBucket::Neutral` and mask a real valence/arousal read whenever
/// `affect_label` is absent but `style` carries its default. `affect_label`
/// has no such default (`None` means "not set", unambiguously); it alone
/// is a safe short-circuit today. Revisit if Phase 3A gives `style` its own
/// `Option` so "unset" and "computed neutral" become distinguishable.
fn emotion_bucket_from_expression(
    expression: &contracts::SpeechExpressionWire,
) -> Option<EmotionBucket> {
    match expression
        .affect_label
        .as_deref()?
        .to_ascii_lowercase()
        .as_str()
    {
        "calm" => Some(EmotionBucket::Calm),
        "warm" => Some(EmotionBucket::Warm),
        "concerned" => Some(EmotionBucket::Concerned),
        "excited" => Some(EmotionBucket::Excited),
        "neutral" => Some(EmotionBucket::Neutral),
        _ => None,
    }
}

/// Shared health signal for the remote GPT-SoVITS engine — the sole synthesis
/// path since local ONNX was removed. Fed by both the live request path
/// (`handle_chat_output`) and the background readiness probe, so an outage is
/// detected even with no user speaking, and a live turn never has to be the
/// first thing to discover the engine is down.
///
/// This deliberately does not gate *whether* a turn gets audio — every failed
/// turn still gets the same-voice fallback vocalization regardless of breaker
/// state (see `handle_chat_output`). The breaker only decides whether to
/// spend a real network round-trip finding that out again, so a confirmed
/// outage does not pay a timeout on every subsequent utterance.
///
/// Read/write across the atomics is not linearised as one operation — the
/// probe task and the request path can interleave. That is an accepted
/// trade-off, not an oversight: `handle_chat_output` is called sequentially
/// from the single `chat.output` subscriber loop in `main`, so the only real
/// race is against the probe, and the worst case is a stale open/closed read
/// for one turn, not a correctness failure.
struct CircuitBreaker {
    consecutive_failures: std::sync::atomic::AtomicU32,
    /// 0 means closed. A non-zero value is the timestamp (ms since epoch) the
    /// breaker opened, so `allow_request` can compute elapsed cooldown without
    /// a separate "when" field that could drift out of sync with the flag.
    opened_at_ms: std::sync::atomic::AtomicU64,
    failure_threshold: u32,
    open_cooldown_ms: u64,
}

impl CircuitBreaker {
    fn new(failure_threshold: u32, open_cooldown_ms: u64) -> Self {
        Self {
            consecutive_failures: std::sync::atomic::AtomicU32::new(0),
            opened_at_ms: std::sync::atomic::AtomicU64::new(0),
            failure_threshold: failure_threshold.max(1),
            open_cooldown_ms,
        }
    }

    /// Whether a real network call is worth attempting right now. `false`
    /// only while open and still inside the cooldown window. The first check
    /// after cooldown elapses returns `true` as a half-open trial: exactly
    /// one caller's result (via `record_success`/`record_failure`) decides
    /// close-vs-reopen, since `handle_chat_output` never calls this
    /// concurrently with itself.
    fn allow_request(&self, now_ms: u64) -> bool {
        let opened = self.opened_at_ms.load(std::sync::atomic::Ordering::SeqCst);
        if opened == 0 {
            return true;
        }
        now_ms.saturating_sub(opened) >= self.open_cooldown_ms
    }

    fn is_open(&self, now_ms: u64) -> bool {
        !self.allow_request(now_ms)
    }

    fn record_success(&self) {
        self.consecutive_failures
            .store(0, std::sync::atomic::Ordering::SeqCst);
        self.opened_at_ms
            .store(0, std::sync::atomic::Ordering::SeqCst);
    }

    /// A half-open trial's failure re-arms the full cooldown with a fresh
    /// timestamp rather than leaving the old one — without this, the very
    /// next `allow_request` call would see an unchanged (now stale) `opened`
    /// timestamp and immediately re-open the trial window, hammering a
    /// confirmed-still-down engine on every turn instead of backing off.
    fn record_failure(&self, now_ms: u64) {
        let failures = self
            .consecutive_failures
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst)
            + 1;
        if failures >= self.failure_threshold {
            self.opened_at_ms
                .store(now_ms.max(1), std::sync::atomic::Ordering::SeqCst);
        }
    }
}

fn now_millis() -> u64 {
    (now_seconds() * 1000.0) as u64
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config = VoiceConfig::from_env();
    warn_if_reference_clip_missing("REF_AUDIO_PATH", &config.emotion_refs.neutral);
    let client = connect_nats(
        &config.nats_url,
        std::env::var("NATS_USER").ok(),
        std::env::var("NATS_PASSWORD").ok(),
    )
    .await
    .with_context(|| format!("connect to NATS at {}", config.nats_url))?;
    let jetstream = async_nats::jetstream::new(client.clone());
    let mut subscriber = client.subscribe(topics::CHAT_OUTPUT).await?;
    let http = Client::builder()
        .connect_timeout(std::time::Duration::from_secs(3))
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .context("build reqwest client with timeouts")?;

    let circuit_breaker = std::sync::Arc::new(CircuitBreaker::new(
        env_or("TTS_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3")
            .parse()
            .unwrap_or(3),
        env_or("TTS_CIRCUIT_BREAKER_COOLDOWN_MS", "15000")
            .parse()
            .unwrap_or(15_000),
    ));
    spawn_readiness_probe(
        config.clone(),
        http.clone(),
        circuit_breaker.clone(),
        env_or("TTS_READINESS_PROBE_INTERVAL_SECS", "45")
            .parse()
            .unwrap_or(45),
    );

    let last_distance = std::sync::Arc::new(std::sync::Mutex::new(1.0));
    let noise_scale_factor = std::sync::Arc::new(std::sync::Mutex::new(1.0f64));
    let noise_floor_moving_avg = std::sync::Arc::new(std::sync::Mutex::new(0.01f64));

    // Subscribe to ambient noise telemetry and track noise floor dynamically
    let noise_scale_clone = noise_scale_factor.clone();
    let noise_avg_clone = noise_floor_moving_avg.clone();
    let mut noise_sub = client.subscribe(topics::AMBIENT_NOISE_TELEMETRY).await?;
    tokio::spawn(async move {
        while let Some(msg) = noise_sub.next().await {
            if let Ok(telemetry) = serde_json::from_slice::<AmbientNoiseTelemetry>(&msg.payload) {
                if let Ok(mut avg_guard) = noise_avg_clone.lock() {
                    *avg_guard = *avg_guard * 0.8 + telemetry.rms_energy * 0.2;
                    let avg = *avg_guard;
                    let scale = if avg < 0.01 {
                        0.7 + (avg / 0.01) * 0.3
                    } else {
                        let excess = (avg - 0.01) / 0.02;
                        1.0 + (excess * 0.5).min(0.5)
                    };
                    if let Ok(mut scale_guard) = noise_scale_clone.lock() {
                        *scale_guard = scale;
                    }
                }
            }
        }
    });

    // Subscribe to vision description and track user distance dynamically
    let last_distance_clone = last_distance.clone();
    let mut vision_sub = client.subscribe(topics::VISION_DESCRIPTION).await?;
    tokio::spawn(async move {
        while let Some(msg) = vision_sub.next().await {
            match serde_json::from_slice::<VisionDescriptionMsg>(&msg.payload) {
                Ok(desc) => {
                    if let Some(dist) = desc.user_distance {
                        if let Ok(mut guard) = last_distance_clone.lock() {
                            *guard = dist;
                        }
                    }
                }
                Err(err) => {
                    warn!(
                        "Failed to deserialize vision description message: {:?}",
                        err
                    );
                }
            }
        }
        warn!("Vision description subscription stream closed.");
    });

    // Abort flag for immediate voice playback stop
    let abort_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let active_turn: ActiveTurn = std::sync::Arc::new(std::sync::Mutex::new(None));
    let attenuation_factor = std::sync::Arc::new(std::sync::Mutex::new(1.0f64));
    let dynamic_prosody = std::sync::Arc::new(std::sync::Mutex::new(None::<ProsodyTrajectory>));
    let hesitation_cache: HesitationCache =
        std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()));

    // Subscribe to audio.stop and set abort flag or duck volume (attenuate)
    let abort_flag_stop = abort_flag.clone();
    let attenuation_stop = attenuation_factor.clone();
    let active_turn_stop = active_turn.clone();
    let mut stop_sub = client.subscribe(topics::AUDIO_STOP).await?;
    tokio::spawn(async move {
        while let Some(msg) = stop_sub.next().await {
            match serde_json::from_slice::<contracts::AudioStop>(&msg.payload) {
                Ok(stop) => {
                    if !mesh_signal_applies_to_active_turn(
                        &active_turn_stop,
                        stop.turn_id.as_deref(),
                    ) {
                        info!(
                            stop_turn = ?stop.turn_id,
                            "Ignoring AUDIO_STOP addressed to a turn that is no longer speaking."
                        );
                        continue;
                    }
                    if stop.speculative {
                        info!("Received SPECULATIVE AUDIO_STOP - ducking current voice playback.");
                        if let Ok(mut guard) = attenuation_stop.lock() {
                            *guard = 0.30;
                        }
                    } else if !stop_aborts_generation(&stop) {
                        info!("Received AUDIO_STOP flush; preserving the active generation.");
                    } else {
                        info!("Received CONFIRMED AUDIO_STOP - aborting current voice playback.");
                        abort_flag_stop.store(true, std::sync::atomic::Ordering::SeqCst);
                    }
                }
                Err(_) => {
                    // Unparseable payloads carry no turn_id to check, so they stay
                    // unscoped and abort whatever is speaking.
                    info!("Received generic AUDIO_STOP - aborting current voice playback.");
                    abort_flag_stop.store(true, std::sync::atomic::Ordering::SeqCst);
                }
            }
        }
    });

    // Subscribe to audio.resume and restore volume (attenuation_factor = 1.0).
    // Phase 2D: turn-scoped like audio.stop above -- a resume delayed in the
    // mesh must not restore volume for a turn that has since genuinely
    // stopped speaking.
    let attenuation_resume = attenuation_factor.clone();
    let active_turn_resume = active_turn.clone();
    let mut resume_sub = client.subscribe(topics::AUDIO_RESUME).await?;
    tokio::spawn(async move {
        while let Some(msg) = resume_sub.next().await {
            match serde_json::from_slice::<contracts::AudioResume>(&msg.payload) {
                Ok(resume) => {
                    if !mesh_signal_applies_to_active_turn(
                        &active_turn_resume,
                        resume.turn_id.as_deref(),
                    ) {
                        info!(
                            resume_turn = ?resume.turn_id,
                            "Ignoring AUDIO_RESUME addressed to a turn that is no longer speaking."
                        );
                        continue;
                    }
                    info!("Received AUDIO_RESUME - restoring voice playback volume.");
                    if let Ok(mut guard) = attenuation_resume.lock() {
                        *guard = 1.0;
                    }
                }
                Err(_) => {
                    // Unparseable payloads carry no turn_id to check, so they
                    // stay unscoped -- same fallback as AUDIO_STOP above.
                    info!("Received generic AUDIO_RESUME - restoring voice playback volume.");
                    if let Ok(mut guard) = attenuation_resume.lock() {
                        *guard = 1.0;
                    }
                }
            }
        }
    });

    // Subscribe to agent.voice.modulation and update dynamic prosody overrides
    let dynamic_prosody_clone = dynamic_prosody.clone();
    let mut modulation_sub = client.subscribe(topics::AGENT_VOICE_MODULATION).await?;
    tokio::spawn(async move {
        while let Some(msg) = modulation_sub.next().await {
            if let Ok(mod_payload) =
                serde_json::from_slice::<contracts::AgentVoiceModulation>(&msg.payload)
            {
                if !mod_payload.trajectory.is_empty() {
                    info!(
                        frames = mod_payload.trajectory.len(),
                        "Received AGENT_VOICE_MODULATION trajectory"
                    );
                    if let Ok(mut guard) = dynamic_prosody_clone.lock() {
                        *guard = Some(ProsodyTrajectory {
                            received_at: std::time::Instant::now(),
                            frames: mod_payload.trajectory,
                        });
                    }
                }
            }
        }
    });

    // #165: block audio ingress until one full synthesis pass has completed, so
    // the model's weights are already resident in VRAM before the first real
    // utterance arrives — otherwise that utterance pays the 2-4s cold-start
    // tensor-load cost itself, before the periodic background probe above (which
    // only starts amortizing it on some later tick) ever gets a chance to.
    // Skipped under the same signal that disables the periodic probe
    // (TTS_READINESS_PROBE_INTERVAL_SECS=0) — local dev against a mock or
    // absent SoVITS server should not have startup blocked on a synthesis call
    // that can never succeed.
    if env_or("TTS_READINESS_PROBE_INTERVAL_SECS", "45")
        .parse()
        .unwrap_or(45u64)
        > 0
    {
        match probe_synthesis(&config, &http).await {
            Ok(()) => info!("TTS warmup pass complete"),
            Err(e) => warn!("TTS warmup pass failed, continuing startup anyway: {e:#}"),
        }
    }

    info!("rust voice-agent subscribed to {}", topics::CHAT_OUTPUT);

    let mut pcm_framer = PcmSampleFramer::new();
    // P4-9: both used to be constructed fresh inside `handle_chat_output`,
    // i.e. once per chat.output chunk rather than once per stream -- unlike
    // `pcm_framer` above, which already gets this right. `ReverbFilter`
    // carries a real delay-line buffer; resetting it every chunk drops the
    // previous chunk's echo tail at every boundary. `current_attenuation_val`
    // is the smoothed *current* value ducking/restoring ramps toward the
    // shared target; resetting it to 1.0 every chunk means a chunk that
    // starts while still ducked flares back up to full volume before ramping
    // back down, audibly, every single chunk boundary during an ongoing duck.
    let mut reverb_filter = ReverbFilter::new((config.sample_rate as f32 * 0.05) as usize, 0.5);
    let mut current_attenuation_val = 1.0f64;
    let mut failed_turns = HashSet::new();

    while let Some(message) = subscriber.next().await {
        match serde_json::from_slice::<ChatOutput>(&message.payload) {
            Ok(event) => {
                if event.done {
                    // End of current stream; safe point to clear interruption state.
                    abort_flag.store(false, std::sync::atomic::Ordering::SeqCst);
                    if let Ok(mut guard) = attenuation_factor.lock() {
                        *guard = 1.0;
                    }
                    if let Ok(mut guard) = active_turn.lock() {
                        *guard = None;
                    }
                    // Bucket 2: per-utterance boundary, not per-chunk -- see
                    // ReverbFilter::reset's doc comment for why per-chunk would be wrong
                    // and why never resetting (the previous behavior) was too.
                    reverb_filter.reset();
                    let failed = take_stream_failure(&mut failed_turns, event.turn_id.as_deref());
                    if let Err(err) = publish_stream_trailer(&jetstream, &event, failed).await {
                        error!("voice-agent failed to publish stream trailer: {err:#}");
                    }
                    continue;
                }

                // Detect start of a new stream/response by tracking turn_id changes
                if let Some(ref current_turn_id) = event.turn_id {
                    let is_new_stream = match active_turn.lock() {
                        Ok(guard) => guard.as_deref() != Some(current_turn_id.as_str()),
                        Err(_) => true,
                    };
                    if is_new_stream {
                        if let Ok(mut guard) = active_turn.lock() {
                            *guard = Some(current_turn_id.clone());
                        }
                        abort_flag.store(false, std::sync::atomic::Ordering::SeqCst);
                        if let Ok(mut guard) = attenuation_factor.lock() {
                            *guard = 1.0;
                        }
                    }
                }

                if abort_flag.load(std::sync::atomic::Ordering::SeqCst) {
                    // Drop trailing chunks after interruption until stream completion.
                    continue;
                }
                let trailer_event = event.clone();
                if let Err(err) = handle_chat_output(
                    &config,
                    &http,
                    &jetstream,
                    event,
                    last_distance.clone(),
                    &mut pcm_framer,
                    &mut reverb_filter,
                    &mut current_attenuation_val,
                    abort_flag.clone(),
                    attenuation_factor.clone(),
                    dynamic_prosody.clone(),
                    noise_scale_factor.clone(),
                    circuit_breaker.clone(),
                    &hesitation_cache,
                )
                .await
                {
                    error!("voice-agent failed to process chat.output: {err:#}");
                    if let Some(turn_id) = trailer_event.turn_id {
                        failed_turns.insert(turn_id);
                    }
                }
            }
            Err(err) => warn!("dropping invalid chat.output payload: {err}"),
        }
    }

    Ok(())
}

fn load_vocalization_pcm(name: &str, sample_rate: u32) -> Vec<u8> {
    let paths = [
        format!("output/{}.wav", name),
        format!("assets/{}.wav", name),
        format!("{}.wav", name),
    ];
    for path in &paths {
        if let Ok(data) = std::fs::read(path) {
            if let Some(pcm) = extract_wav_data(&data) {
                info!("Successfully loaded vocalization {} from {}", name, path);
                return pcm;
            } else {
                warn!(
                    "WAV file at {} found, but missing data chunk or invalid format.",
                    path
                );
            }
        }
    }
    // P4-9: this used to generate a synthetic sine/LCG-noise buzz here -- a
    // sound in a voice that is neither the cloned voice nor silence,
    // contradicting the no-fallback-voice principle this file states
    // elsewhere (see `TemporalPart::Text`'s comment). A named degradation
    // (logged) plus silence of the same nominal duration is honest about
    // what happened; a buzz reads as a bug, not a missing asset.
    warn!(
        "Vocalization asset not found: {}. Playing silence instead of a synthetic buzz.",
        name
    );
    contracts::silence_pcm(500, sample_rate)
}

fn extract_wav_data(data: &[u8]) -> Option<Vec<u8>> {
    // Minimal RIFF/WAV parser: find "data" chunk and return its contents
    if data.len() < 12 || &data[0..4] != b"RIFF" || &data[8..12] != b"WAVE" {
        return None;
    }
    let mut pos = 12;
    while pos + 8 <= data.len() {
        let chunk_id = &data[pos..pos + 4];
        let chunk_size =
            u32::from_le_bytes([data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7]])
                as usize;
        if chunk_id == b"data" {
            let start = pos + 8;
            let end = (start + chunk_size).min(data.len());
            return Some(data[start..end].to_vec());
        }
        pos += 8 + chunk_size;
        if chunk_size % 2 != 0 {
            pos += 1; // RIFF chunks are word-aligned
        }
    }
    None
}

/// A short, natural non-verbal hesitation sound -- not a word, just the
/// "mm" a person makes mid-thought. Synthesized through the real cloned
/// voice like any other text, so `<hesitate>` no longer speaks in a
/// different, synthetic voice than the rest of the turn.
const HESITATION_FILLER_TEXT: &str = "Mm...";

/// One cached PCM buffer per delivery register (`EmotionBucket`), so a
/// hesitation later in the same turn -- or in a later turn with the same
/// register -- reuses the already-synthesized audio instead of paying a
/// second real-TTS round-trip for what is deliberately always the same
/// short phrase. Bounded by construction: there are exactly five buckets,
/// so this can never grow past five entries.
type HesitationCache =
    std::sync::Arc<tokio::sync::Mutex<std::collections::HashMap<EmotionBucket, Vec<u8>>>>;

/// P4-9: `<hesitate>` used to synthesize a sine+noise buzz locally --
/// audio in neither the cloned voice nor silence, the same contradiction
/// `load_vocalization_pcm`'s missing-asset fallback had. Routes through the
/// real TTS engine instead, cached per delivery register so the latency a
/// hesitation exists to cover is not doubled by the call covering it.
///
/// Falls back to silence of `duration_ms` -- never a buzz -- when the
/// circuit breaker is open (a known-down engine) or when this specific
/// synthesis attempt fails; a failed attempt records on the same breaker
/// real speech does, since a TTS engine down for one is down for both.
async fn hesitation_pcm(
    config: &VoiceConfig,
    http: &Client,
    circuit_breaker: &CircuitBreaker,
    cache: &HesitationCache,
    bucket: EmotionBucket,
    ref_clip: &RefClip,
    duration_ms: u32,
    sample_rate: u32,
    prosody: &contracts::Prosody,
) -> Vec<u8> {
    if let Some(cached) = cache.lock().await.get(&bucket) {
        return cached.clone();
    }

    let now_ms = now_millis();
    if circuit_breaker.is_open(now_ms) {
        return contracts::silence_pcm(duration_ms, sample_rate);
    }

    let result = synthesize_stream_with_retry(
        config,
        http,
        HESITATION_FILLER_TEXT,
        ref_clip,
        prosody.rate,
        prosody.pitch,
        prosody.volume,
    )
    .await;

    let mut response = match result {
        Ok(response) => response,
        // Reviewer finding: this catch-all used to also match a
        // SynthesisRejected validation error, opening the circuit breaker
        // for a rejected filler even though GPT-SoVITS is healthy. Mirrors
        // the main synthesis call site's own arm: a validation rejection
        // says nothing about whether the engine is up.
        Err(e) if e.downcast_ref::<SynthesisRejected>().is_some() => {
            // Already logged loudly, with the rejected text, inside
            // synthesize_stream_with_retry.
            return contracts::silence_pcm(duration_ms, sample_rate);
        }
        Err(e) => {
            warn!("hesitation synthesis failed, playing silence instead: {e:#}");
            circuit_breaker.record_failure(now_ms);
            return contracts::silence_pcm(duration_ms, sample_rate);
        }
    };

    let mut pcm = Vec::new();
    loop {
        match response.chunk().await {
            Ok(Some(chunk)) => pcm.extend_from_slice(&chunk),
            Ok(None) => break,
            Err(e) => {
                warn!(
                    "hesitation synthesis stream failed mid-read, playing silence instead: {e:#}"
                );
                circuit_breaker.record_failure(now_ms);
                return contracts::silence_pcm(duration_ms, sample_rate);
            }
        }
    }

    if pcm.is_empty() {
        warn!("hesitation synthesis returned no audio, playing silence instead");
        circuit_breaker.record_failure(now_ms);
        return contracts::silence_pcm(duration_ms, sample_rate);
    }

    circuit_breaker.record_success();
    cache.lock().await.insert(bucket, pcm.clone());
    pcm
}

fn apply_attenuation(pcm: &mut [u8], current_val: &mut f64, target_val: f64) {
    if pcm.len() < 2 {
        return;
    }
    let mut samples = pcm
        .chunks_exact(2)
        .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
        .collect::<Vec<i16>>();

    let num_samples = samples.len();
    let step = (target_val - *current_val) / num_samples as f64;

    for sample in samples.iter_mut() {
        *current_val += step;
        let val = *sample as f64 * (*current_val);
        *sample = val.clamp(i16::MIN as f64, i16::MAX as f64) as i16;
    }

    *current_val = target_val; // Ensure we land exactly at target

    let mut idx = 0;
    for s in samples {
        let bytes = s.to_le_bytes();
        pcm[idx] = bytes[0];
        pcm[idx + 1] = bytes[1];
        idx += 2;
    }
}

fn generate_and_publish_visemes(
    jetstream: &async_nats::jetstream::Context,
    pcm: &[u8],
) -> Result<()> {
    if pcm.is_empty() {
        return Ok(());
    }
    let samples = pcm
        .chunks_exact(2)
        .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
        .collect::<Vec<i16>>();
    let num_samples = samples.len();
    if num_samples == 0 {
        return Ok(());
    }
    let sum_sq: f64 = samples
        .iter()
        .map(|&s| {
            let norm = s as f64 / i16::MAX as f64;
            norm * norm
        })
        .sum();
    let rms = (sum_sq / num_samples as f64).sqrt();
    let target_level = (rms * 10.0).min(1.0); // scale up for visualization

    let viseme_id = if target_level < 0.05 {
        "sil".to_string()
    } else if target_level < 0.3 {
        "AA".to_string()
    } else if target_level < 0.6 {
        "O".to_string()
    } else {
        "AH".to_string()
    };

    let viseme = PlaybackVisemes {
        target_level,
        viseme_id,
        timestamp: now_seconds(),
    };

    let jetstream = jetstream.clone();
    tokio::spawn(async move {
        let _ = jetstream
            .publish(
                topics::AUDIO_PLAYBACK_VISEMES,
                Bytes::from(serde_json::to_vec(&viseme).unwrap()),
            )
            .await;
    });

    Ok(())
}

async fn handle_chat_output(
    config: &VoiceConfig,
    http: &Client,
    jetstream: &async_nats::jetstream::Context,
    event: ChatOutput,
    last_distance: std::sync::Arc<std::sync::Mutex<f64>>,
    pcm_framer: &mut PcmSampleFramer,
    reverb_filter: &mut ReverbFilter,
    current_attenuation_val: &mut f64,
    abort_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    attenuation_factor: std::sync::Arc<std::sync::Mutex<f64>>,
    dynamic_prosody: std::sync::Arc<std::sync::Mutex<Option<ProsodyTrajectory>>>,
    noise_scale_factor: std::sync::Arc<std::sync::Mutex<f64>>,
    circuit_breaker: std::sync::Arc<CircuitBreaker>,
    hesitation_cache: &HesitationCache,
) -> Result<()> {
    if event.done {
        return Ok(());
    }

    let Some(content) = event
        .content
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    else {
        return Ok(());
    };

    let prosody = clamp_prosody(if let Ok(guard) = dynamic_prosody.lock() {
        guard
            .as_ref()
            .and_then(ProsodyTrajectory::prosody_now)
            .unwrap_or_else(|| vad_to_prosody(event.affect.as_ref()))
    } else {
        vad_to_prosody(event.affect.as_ref())
    });

    // Computed once per event, like prosody above: the delivery register does
    // not change mid-utterance, only which reference clip carries it. `bucket`
    // doubles as the hesitation cache key below.
    let bucket = select_emotion_bucket(event.affect.as_ref(), event.expression.as_ref());
    let ref_clip = config.emotion_refs.resolve(bucket).clone();

    let distance = event
        .affect
        .as_ref()
        .and_then(|a| a.user_distance)
        .unwrap_or_else(|| {
            if let Ok(guard) = last_distance.lock() {
                *guard
            } else {
                1.0
            }
        });

    let parts = match event.expression.as_ref() {
        Some(expression) => temporal_parts_from_expression(content, expression),
        None => split_temporal_parts(content)?,
    };
    for part in parts {
        if abort_flag.load(std::sync::atomic::Ordering::SeqCst) {
            info!("Aborting playback due to AUDIO_STOP event.");
            break;
        }
        match part {
            TemporalPart::Silence(ms) => {
                pcm_framer.clear_history();
                let pcm = contracts::silence_pcm(ms, config.sample_rate);
                if let Ok(guard) = attenuation_factor.lock() {
                    *current_attenuation_val = *guard;
                }
                let noise_scale = if let Ok(guard) = noise_scale_factor.lock() {
                    *guard
                } else {
                    1.0
                };
                publish_pcm(jetstream, pcm, &event, noise_scale).await?;
            }
            TemporalPart::Vocalization(name) => {
                pcm_framer.clear_history();
                let mut pcm = load_vocalization_pcm(&name, config.sample_rate);
                pcm = reverb_filter.process(&pcm, reverb_wet_gain_for_distance(distance));

                let target_att = if let Ok(guard) = attenuation_factor.lock() {
                    *guard
                } else {
                    1.0
                };
                apply_attenuation(&mut pcm, current_attenuation_val, target_att);
                let _ = generate_and_publish_visemes(jetstream, &pcm);

                let gain = utterance_gain(&noise_scale_factor, prosody.volume);
                publish_pcm(jetstream, pcm, &event, gain).await?;
            }
            TemporalPart::Hesitation(ms) => {
                pcm_framer.clear_history();
                let mut pcm = hesitation_pcm(
                    config,
                    http,
                    &circuit_breaker,
                    hesitation_cache,
                    bucket,
                    &ref_clip,
                    ms,
                    config.sample_rate,
                    &prosody,
                )
                .await;
                pcm = reverb_filter.process(&pcm, reverb_wet_gain_for_distance(distance));

                let target_att = if let Ok(guard) = attenuation_factor.lock() {
                    *guard
                } else {
                    1.0
                };
                apply_attenuation(&mut pcm, current_attenuation_val, target_att);
                let _ = generate_and_publish_visemes(jetstream, &pcm);

                let gain = utterance_gain(&noise_scale_factor, prosody.volume);
                publish_pcm(jetstream, pcm, &event, gain).await?;
            }
            TemporalPart::Text(text) => {
                // Local ONNX synthesis was removed (2026-07): it was a fallback to a
                // different, uncloned voice, which is strictly worse than silence
                // under the no-fallback requirement — see the ledger entry. Remote
                // synthesis is the only real-speech path now; a confirmed failure
                // gets the same-voice fallback vocalization below, never a
                // different voice.
                let now_ms = now_millis();
                let response = if circuit_breaker.is_open(now_ms) {
                    // Already known down, from a recent live failure or the
                    // background probe — skip the round-trip and its timeout
                    // rather than rediscovering the same outage every turn.
                    None
                } else {
                    match synthesize_stream_with_retry(
                        config,
                        http,
                        &text,
                        &ref_clip,
                        prosody.rate,
                        prosody.pitch,
                        prosody.volume,
                    )
                    .await
                    {
                        Ok(response) => {
                            circuit_breaker.record_success();
                            Some(response)
                        }
                        Err(e) if e.downcast_ref::<SynthesisRejected>().is_some() => {
                            // Already logged loudly, with the rejected text,
                            // inside synthesize_stream_with_retry. Bucket 4:
                            // a validation rejection says nothing about
                            // whether the engine is up, so it must not open
                            // the circuit breaker the way a real outage does.
                            None
                        }
                        Err(e) => {
                            error!("synthesis failed after retries: {e:#}");
                            circuit_breaker.record_failure(now_ms);
                            None
                        }
                    }
                };

                let Some(mut response) = response else {
                    // No network call, or one that failed after retries: play
                    // the same-voice "one moment" vocalization instead of
                    // dropping the turn silently. `load_vocalization_pcm`
                    // degrades to silence (P4-9), never a different voice or
                    // a synthetic buzz, if `voice_engine_unavailable.wav` has
                    // not been recorded yet — safe before the cloned voice
                    // exists, better once it does.
                    pcm_framer.clear_history();
                    let mut pcm =
                        load_vocalization_pcm("voice_engine_unavailable", config.sample_rate);
                    pcm = reverb_filter.process(&pcm, reverb_wet_gain_for_distance(distance));

                    let target_att = if let Ok(guard) = attenuation_factor.lock() {
                        *guard
                    } else {
                        1.0
                    };
                    apply_attenuation(&mut pcm, current_attenuation_val, target_att);
                    let _ = generate_and_publish_visemes(jetstream, &pcm);

                    let gain = utterance_gain(&noise_scale_factor, prosody.volume);
                    publish_pcm(jetstream, pcm, &event, gain).await?;
                    continue;
                };

                while let Some(chunk) = response.chunk().await? {
                    if abort_flag.load(std::sync::atomic::Ordering::SeqCst) {
                        info!("Aborting synthesis chunk stream due to AUDIO_STOP event.");
                        break;
                    }
                    if !chunk.is_empty() {
                        let mut pcm_bytes = chunk.to_vec();

                        // Ambient compensation only: `synthesize_stream` already
                        // sent rate/pitch/volume to the remote engine, so folding
                        // `prosody.volume` in here would apply it twice.
                        let noise_scale = if let Ok(guard) = noise_scale_factor.lock() {
                            *guard
                        } else {
                            1.0
                        };

                        pcm_bytes = reverb_filter
                            .process(&pcm_bytes, reverb_wet_gain_for_distance(distance));
                        pcm_bytes = pcm_framer.process(&pcm_bytes);

                        let target_att = if let Ok(guard) = attenuation_factor.lock() {
                            *guard
                        } else {
                            1.0
                        };
                        apply_attenuation(&mut pcm_bytes, current_attenuation_val, target_att);
                        let _ = generate_and_publish_visemes(jetstream, &pcm_bytes);

                        publish_pcm(jetstream, pcm_bytes, &event, noise_scale).await?;
                    }
                }
            }
        }
    }

    Ok(())
}

#[derive(Debug, PartialEq)]
enum TemporalPart {
    Text(String),
    Silence(u32),
    Vocalization(String),
    Hesitation(u32),
}

/// Structured expression supplies temporal delivery when present on ChatOutput.
/// Content remains one plain-text part: parsing inline delivery tags as well
/// would double-apply pauses and vocalizations already encoded by expression.
/// Messages without expression retain the legacy inline-tag parser.
///
/// Thresholds mirror the legacy tags' all-or-nothing behavior at a
/// continuous value -- `<hesitate>` was a fixed 350ms pause and
/// `<breath_fast>` a fixed vocalization, both firing only past
/// `_emit_validated`'s dominance threshold rather than scaling from zero.
/// Past the floor, duration scales linearly from `BASE_MS` (roughly the
/// legacy fixed value, at just-past-floor intensity) up to `MAX_MS` (at
/// `hesitation == 1.0`) rather than staying pinned at the old flat value --
/// a first-pass mapping, not a validated one, same as `select_emotion_bucket`
/// above: retune once real `SpeechExpression` values exist to listen to.
const EXPRESSION_HESITATION_FLOOR: f64 = 0.35;
const EXPRESSION_HESITATION_BASE_MS: f64 = 350.0;
const EXPRESSION_HESITATION_MAX_MS: u32 = 900;

/// Stage 3 (Blocker 2, Codex's Stage 2 review): `breath` is not a single
/// on/off signal -- Codex 3A's `_breath_level` (`cognitive/expression.py`)
/// encodes two distinct legacy tags at two distinct levels: the low-arousal,
/// negative-valence `<sigh_soft>` case at `0.5`, and the high-arousal,
/// negative-valence `<breath_fast>` case at `1.0`. A single `>= floor` check
/// collapsed both into `breath_fast`, silently turning a soft sigh into a
/// fast, distressed breath. Two bands preserve both: `[SOFT_FLOOR, FAST_FLOOR)`
/// is `sigh_soft`, `[FAST_FLOOR, 1.0]` is `breath_fast` -- `0.75` sits
/// strictly between the two known legacy values (0.5, 1.0), same reasoning
/// as `EXPRESSION_HESITATION_FLOOR` sitting strictly between "no signal" and
/// a real one.
const EXPRESSION_BREATH_SOFT_FLOOR: f64 = 0.25;
const EXPRESSION_BREATH_FAST_FLOOR: f64 = 0.75;

fn temporal_parts_from_expression(
    content: &str,
    expression: &contracts::SpeechExpressionWire,
) -> Vec<TemporalPart> {
    let mut parts = Vec::new();

    if expression.hesitation >= EXPRESSION_HESITATION_FLOOR {
        let span = EXPRESSION_HESITATION_MAX_MS as f64 - EXPRESSION_HESITATION_BASE_MS;
        let ms = EXPRESSION_HESITATION_BASE_MS + span * expression.hesitation.min(1.0);
        parts.push(TemporalPart::Hesitation(
            (ms as u32).min(EXPRESSION_HESITATION_MAX_MS),
        ));
    }
    if expression.breath >= EXPRESSION_BREATH_FAST_FLOOR {
        parts.push(TemporalPart::Vocalization("breath_fast".to_string()));
    } else if expression.breath >= EXPRESSION_BREATH_SOFT_FLOOR {
        parts.push(TemporalPart::Vocalization("sigh_soft".to_string()));
    }

    push_text(&mut parts, content);
    parts
}

fn split_temporal_parts(text: &str) -> Result<Vec<TemporalPart>> {
    let re = Regex::new(r"(<pause=\d+ms>|<hesitate>|<breath_fast>|<sigh_soft>)")?;
    let mut parts = Vec::new();
    let mut last = 0;

    for mat in re.find_iter(text) {
        if mat.start() > last {
            push_text(&mut parts, &text[last..mat.start()]);
        }
        let token = mat.as_str();
        if token == "<hesitate>" {
            parts.push(TemporalPart::Hesitation(350));
        } else if token == "<breath_fast>" {
            parts.push(TemporalPart::Vocalization("breath_fast".to_string()));
        } else if token == "<sigh_soft>" {
            parts.push(TemporalPart::Vocalization("sigh_soft".to_string()));
        } else {
            let ms = token
                .trim_start_matches("<pause=")
                .trim_end_matches("ms>")
                .parse::<u32>()
                .context("parse pause duration")?;
            let clamped_ms = ms.min(5000);
            parts.push(TemporalPart::Silence(clamped_ms));
        }
        last = mat.end();
    }

    if last < text.len() {
        push_text(&mut parts, &text[last..]);
    }

    Ok(merge_degenerate_text_fragments(parts))
}

fn push_text(parts: &mut Vec<TemporalPart>, text: &str) {
    let text = text.trim();
    if !text.is_empty() {
        parts.push(TemporalPart::Text(text.to_string()));
    }
}

/// A `TemporalPart::Text` fragment with no alphanumeric content at all --
/// just punctuation and/or whitespace. GPT-SoVITS rejects these outright
/// ("Please enter valid text."), so sending one as its own synthesis call
/// either wastes a round-trip or, worse, gets mistaken for the engine being
/// down (see `SynthesisRejected` below).
fn is_degenerate_fragment(text: &str) -> bool {
    !text.chars().any(|c| c.is_alphanumeric())
}

/// Bucket 4 (VOICE_REMEDIATION_PLAN.md): a leftover "...", "-", or similar
/// shows up whenever a `<pause>`/`<hesitate>`/`<breath_fast>`/`<sigh_soft>`
/// token isolates trailing punctuation between two real words -- by
/// construction, that token is *why* the fragment became its own
/// `TemporalPart::Text` instead of being part of a neighbouring clause's
/// string in the first place, so a degenerate fragment is always separated
/// from its nearest real text by at least one such token. Rather than send
/// it to synthesis alone -- where GPT-SoVITS rejects it outright -- splice
/// its characters onto the *content* of the nearest real `Text` part,
/// previous preferred (trailing punctuation usually belongs to the clause
/// before it), searching past intervening `Silence`/`Hesitation`/
/// `Vocalization` entries without moving or removing any of them: the pause
/// stays exactly where it was, only the adjacent clause's text gains a
/// trailing (or leading) `"..."`/`"-"`, which most TTS front ends read as a
/// legitimate trailing-off or clipped-word cue rather than as noise. A
/// fragment with no real `Text` part anywhere in the sequence (the whole
/// input was punctuation) has nothing to merge into and is dropped, loudly.
fn merge_degenerate_text_fragments(mut parts: Vec<TemporalPart>) -> Vec<TemporalPart> {
    let mut i = 0;
    while i < parts.len() {
        let degenerate = matches!(&parts[i], TemporalPart::Text(t) if is_degenerate_fragment(t));
        if !degenerate {
            i += 1;
            continue;
        }
        let text = match parts.remove(i) {
            TemporalPart::Text(t) => t,
            _ => unreachable!("just matched TemporalPart::Text above"),
        };

        let merged_backward = parts[..i].iter_mut().rev().find_map(|p| match p {
            TemporalPart::Text(t) => Some(t),
            _ => None,
        });
        if let Some(prev) = merged_backward {
            prev.push(' ');
            prev.push_str(&text);
            continue; // don't advance i -- re-examine what now sits here
        }

        let merged_forward = parts[i..].iter_mut().find_map(|p| match p {
            TemporalPart::Text(t) => Some(t),
            _ => None,
        });
        if let Some(next) = merged_forward {
            let mut combined = text;
            combined.push(' ');
            combined.push_str(next);
            *next = combined;
            continue;
        }

        warn!(
            fragment = %text,
            "dropping a punctuation-only text fragment with no speakable text anywhere to merge into"
        );
        // Already removed via `parts.remove(i)` above; don't advance --
        // whatever shifted into this slot needs to be examined too.
    }

    parts
}

/// GPT-SoVITS rejected the input text itself -- its own validation ("Please
/// enter valid text.", HTTP 400) on a fragment it considers unspeakable --
/// rather than failing to reach or process the request. Bucket 4
/// (VOICE_REMEDIATION_PLAN.md): worth telling apart from a transient
/// network/server failure, because retrying identical text against a
/// deterministic validator reproduces the identical rejection every time,
/// and because it says nothing about whether the engine itself is healthy --
/// `synthesize_stream_with_retry` and the circuit breaker both key off this
/// type to skip retry/failure-counting for exactly this case.
#[derive(Debug)]
struct SynthesisRejected {
    status: reqwest::StatusCode,
    body: String,
}

impl std::fmt::Display for SynthesisRejected {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "SoVITS rejected input text (HTTP {}): {}",
            self.status, self.body
        )
    }
}

impl std::error::Error for SynthesisRejected {}

async fn synthesize_stream(
    config: &VoiceConfig,
    http: &Client,
    text: &str,
    ref_clip: &RefClip,
    speed: f64,
    pitch: f64,
    volume: f64,
) -> Result<reqwest::Response> {
    let payload = json!({
        "text": text,
        "text_lang": config.tts_language,
        "ref_audio_path": ref_clip.audio_path,
        "prompt_text": ref_clip.text,
        "prompt_lang": config.tts_language,
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "raw",
        "streaming_mode": 1,
        "speed_factor": speed,
        "pitch": pitch,
        "volume": volume,
    });

    let url = format!("{}/tts", config.sovits_url.trim_end_matches('/'));
    let response = http.post(url).json(&payload).send().await?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        if status == reqwest::StatusCode::BAD_REQUEST && body.to_lowercase().contains("valid text")
        {
            return Err(SynthesisRejected { status, body }.into());
        }
        anyhow::bail!("SoVITS returned HTTP {status}: {body}");
    }

    Ok(response)
}

/// How many times a synthesis request is attempted before it counts as one
/// circuit-breaker failure. Retries only cover the *pre-flight* request
/// (connecting and getting a response back) — once a stream has started
/// delivering chunks, a mid-stream drop is not retried here, since replaying
/// the request would re-speak audio already played for this turn.
const MAX_SYNTHESIS_ATTEMPTS: u32 = 3;
const RETRY_BACKOFF_MS: [u64; 2] = [150, 400];

async fn synthesize_stream_with_retry(
    config: &VoiceConfig,
    http: &Client,
    text: &str,
    ref_clip: &RefClip,
    speed: f64,
    pitch: f64,
    volume: f64,
) -> Result<reqwest::Response> {
    let mut last_err = None;
    for attempt in 0..MAX_SYNTHESIS_ATTEMPTS {
        match synthesize_stream(config, http, text, ref_clip, speed, pitch, volume).await {
            Ok(response) => return Ok(response),
            Err(e) if e.downcast_ref::<SynthesisRejected>().is_some() => {
                // Bucket 4: a validation rejection is deterministic -- attempt
                // 2 and 3 would fail identically, so stop here instead of
                // burning the rest of the retry budget and its backoff
                // delays. Logged loudly with the actual rejected text, since
                // this is the one failure mode where the fragment is truly
                // never going to be spoken.
                error!(
                    text = %text,
                    error = %e,
                    "synthesis rejected this text as invalid -- not retrying, this fragment will not be spoken"
                );
                return Err(e);
            }
            Err(e) => {
                warn!(
                    attempt = attempt + 1,
                    max_attempts = MAX_SYNTHESIS_ATTEMPTS,
                    error = %e,
                    "synthesis request failed"
                );
                last_err = Some(e);
                if let Some(&delay_ms) = RETRY_BACKOFF_MS.get(attempt as usize) {
                    tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
                }
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow::anyhow!("synthesis failed with no captured error")))
}

/// A short, fixed phrase used only to prove the engine can actually render
/// audio right now — never spoken to a user.
const READINESS_PROBE_PHRASE: &str = "Status check.";

/// One readiness check: synthesize the probe phrase and confirm real audio
/// bytes come back, not just a 200 status. A streaming TTS server can accept
/// a request and start a 200 response before generation actually succeeds, so
/// checking only the status — as the previous Docker healthcheck did with a
/// plain `/docs` ping — misses exactly the "up but broken" failure mode this
/// exists to catch (GPT-SoVITS has open reports of blank-audio responses
/// under streaming load).
async fn probe_synthesis(config: &VoiceConfig, http: &Client) -> Result<()> {
    let mut response = synthesize_stream(
        config,
        http,
        READINESS_PROBE_PHRASE,
        &config.emotion_refs.neutral,
        1.0,
        1.0,
        1.0,
    )
    .await?;

    match response
        .chunk()
        .await
        .context("reading readiness-probe response body")?
    {
        Some(bytes) if !bytes.is_empty() => Ok(()),
        _ => anyhow::bail!("readiness probe got an empty response body"),
    }
}

/// Background task: proves the engine works independently of live traffic, so
/// an outage is caught (and recovery detected) even during silence, and a
/// live utterance is never the first thing to discover the engine is down.
/// `interval_secs == 0` disables the probe — useful for local development
/// against a mock or absent SoVITS server, where a probe would just spam
/// warnings every tick for no operational benefit.
fn spawn_readiness_probe(
    config: VoiceConfig,
    http: Client,
    breaker: std::sync::Arc<CircuitBreaker>,
    interval_secs: u64,
) {
    if interval_secs == 0 {
        info!("TTS readiness probe disabled (TTS_READINESS_PROBE_INTERVAL_SECS=0)");
        return;
    }
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(std::time::Duration::from_secs(interval_secs));
        loop {
            ticker.tick().await;
            let now_ms = now_millis();
            match probe_synthesis(&config, &http).await {
                Ok(()) => breaker.record_success(),
                Err(e) => {
                    warn!("TTS readiness probe failed: {e:#}");
                    breaker.record_failure(now_ms);
                }
            }
        }
    });
}

/// Publish gain for locally-generated PCM that has *not* already had
/// `Prosody.volume` baked in (vocalisations, hesitations): emotional level x
/// ambient-noise compensation.
///
/// Without this, a quiet agent's "hmm" would play at full level next to its
/// attenuated words, breaking the illusion mid-utterance.
fn utterance_gain(noise_scale_factor: &std::sync::Mutex<f64>, volume: f64) -> f64 {
    let noise_scale = noise_scale_factor.lock().map(|g| *g).unwrap_or(1.0);
    volume * noise_scale
}

fn scale_pcm_in_place(pcm: &mut [u8], noise_scale: f64) {
    if noise_scale != 1.0 && pcm.len() >= 2 {
        let mut samples = pcm
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        for sample in samples.iter_mut() {
            let val = *sample as f64 * noise_scale;
            *sample = val.clamp(i16::MIN as f64, i16::MAX as f64) as i16;
        }

        let mut idx = 0;
        for s in samples {
            let bytes = s.to_le_bytes();
            pcm[idx] = bytes[0];
            pcm[idx + 1] = bytes[1];
            idx += 2;
        }
    }
}

async fn publish_pcm(
    jetstream: &async_nats::jetstream::Context,
    mut pcm: Vec<u8>,
    event: &ChatOutput,
    noise_scale: f64,
) -> Result<()> {
    scale_pcm_in_place(&mut pcm, noise_scale);

    let mut headers = HeaderMap::new();
    headers.insert(HEADER_PAYLOAD_FORMAT, PAYLOAD_FORMAT_RAW_PCM);
    headers.insert(
        HEADER_LATENCY_META,
        build_latency_metadata(event).to_string(),
    );

    // audit/ROADMAP.md P2-2 (M3-P3): this used to be `.await?.await?` -- the
    // second await being JetStream's publish ack, i.e. a full server round-trip
    // *per PCM chunk*, on the outbound speech path. Worse than the cost, it
    // serialized: chunk N+1 was not even sent until chunk N had been
    // acknowledged, so the ack latency was added to time-to-first-audio and to
    // every gap after it.
    //
    // The roadmap frames the fix as applying the maintainer's own inbound fix
    // in the other direction. **The pattern matches but the payloads do not**,
    // and the difference matters. Inbound (`stt-agent`, `user.voice_properties`)
    // drops the ack on "ephemeral observability samples superseded by the next
    // chunk" -- losing one costs a metric. This is the agent's actual speech:
    // losing a chunk is an audible gap in what the user hears. So the ack is
    // not simply discarded here.
    //
    // The first `await?` is kept, and still surfaces send-side failure
    // (connection down, no responders) to the caller as before. The ack is
    // moved off the critical path rather than dropped: awaited on a spawned
    // task, so a stream that is rejecting messages -- full, storage error --
    // is still reported, just not waited for. The residual trade, stated
    // plainly: a chunk that the server never accepts is now noticed a moment
    // *after* the fact, in a log line, instead of being returned to the caller
    // as an error at the point of publish.
    let ack = jetstream
        .publish_with_headers(topics::AUDIO_STREAM, headers, Bytes::from(pcm))
        .await?;

    tokio::spawn(async move {
        if let Err(err) = ack.await {
            warn!(
                error = %err,
                subject = topics::AUDIO_STREAM,
                "JetStream did not acknowledge an outbound audio chunk; \
                 the listener may hear a gap"
            );
        }
    });

    Ok(())
}

async fn publish_stream_trailer(
    jetstream: &async_nats::jetstream::Context,
    event: &ChatOutput,
    failed: bool,
) -> Result<()> {
    let turn_id = event.turn_id.clone().unwrap_or_default();
    let trailer = AudioStreamTrailer {
        kind: "END_OF_STREAM".to_string(),
        utterance_id: turn_id.clone(),
        turn_id,
        failed,
    };
    let mut meta = build_latency_metadata(event);
    if let Some(object) = meta.as_object_mut() {
        object.insert("audio_stream_kind".to_string(), json!("trailer"));
    }
    let mut headers = HeaderMap::new();
    headers.insert(HEADER_PAYLOAD_FORMAT, "application/json");
    headers.insert(HEADER_LATENCY_META, meta.to_string());
    let ack = jetstream
        .publish_with_headers(
            topics::AUDIO_STREAM,
            headers,
            Bytes::from(serde_json::to_vec(&trailer)?),
        )
        .await?;
    tokio::spawn(async move {
        if let Err(err) = ack.await {
            warn!(error = %err, "JetStream did not acknowledge the outbound audio trailer");
        }
    });
    Ok(())
}

fn build_latency_metadata(event: &ChatOutput) -> serde_json::Value {
    let now = now_seconds();
    let mut meta = event
        .latency_metadata
        .as_ref()
        .map(serde_json::to_value)
        .and_then(Result::ok)
        .unwrap_or_else(|| json!({"start_time": now, "hops": [], "source": "voice_agent"}));

    if let Some(obj) = meta.as_object_mut() {
        obj.entry("start_time").or_insert(json!(now));
        obj.entry("source").or_insert(json!("voice_agent"));
        // P1-3: transport_agent has no other way to know which turn a queued
        // PCM chunk belongs to, and needs that to decide whether a confirmed
        // audio.stop for an *older* turn should flush a queue that already
        // holds the *next* one. Always the current event's turn_id, not
        // inherited -- a carried-over `latency_metadata` blob from upstream
        // must not leave a stale value here.
        obj.insert("turn_id".to_string(), json!(event.turn_id));
        obj.insert("utterance_id".to_string(), json!(event.turn_id));
        if event.done {
            obj.insert(
                "character_offset".to_string(),
                json!(event
                    .full_response
                    .as_deref()
                    .unwrap_or_default()
                    .chars()
                    .count()),
            );
            obj.insert(
                "word_index".to_string(),
                json!(event
                    .full_response
                    .as_deref()
                    .unwrap_or_default()
                    .split_whitespace()
                    .count()),
            );
        }
        // P4-2: pass-through, not computed here -- brain_agent already knows
        // where this chunk's text ends within the true response
        // (`_char_offset_after_word`), and this process has no way to
        // recompute that itself (it only ever sees one chunk's `content` at
        // a time, never the accumulating full response). transport_agent
        // relays these onward as `audio.playback.progress` once the PCM
        // carrying them has actually reached the LiveKit audio source.
        if let Some(offset) = event.metadata.get("character_offset") {
            obj.insert("character_offset".to_string(), offset.clone());
        }
        if let Some(word_index) = event.metadata.get("word_index") {
            obj.insert("word_index".to_string(), word_index.clone());
        }
        let hops = obj.entry("hops").or_insert(json!([]));
        if let Some(hops) = hops.as_array_mut() {
            hops.push(json!({
                "agent": "voice_agent",
                "subject": topics::AUDIO_STREAM,
                "timestamp": now,
            }));
        }
    }

    meta
}

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---------------------------------------------------------- Phase 2D: turn-scoped audio.resume

    #[test]
    fn mesh_signal_with_no_turn_id_is_always_applied() {
        let active: ActiveTurn =
            std::sync::Arc::new(std::sync::Mutex::new(Some("turn-1".to_string())));
        assert!(mesh_signal_applies_to_active_turn(&active, None));
    }

    #[test]
    fn self_correction_flush_does_not_abort_the_active_generation() {
        let stop: contracts::AudioStop =
            serde_json::from_str(r#"{"interrupt":true,"flush":true,"turn_id":"turn-1"}"#).unwrap();
        assert!(!stop_aborts_generation(&stop));
    }

    #[test]
    fn failed_audio_stream_marks_its_terminal_trailer_once() {
        let mut failed_turns = HashSet::from(["turn-1".to_string()]);

        assert!(take_stream_failure(&mut failed_turns, Some("turn-1")));
        assert!(!take_stream_failure(&mut failed_turns, Some("turn-1")));
        assert!(!take_stream_failure(&mut failed_turns, Some("turn-2")));
        assert!(!take_stream_failure(&mut failed_turns, None));
    }

    #[test]
    fn ordinary_confirmed_stop_still_aborts_the_active_generation() {
        let stop: contracts::AudioStop =
            serde_json::from_str(r#"{"interrupt":true,"turn_id":"turn-1"}"#).unwrap();
        assert!(stop_aborts_generation(&stop));
    }

    #[test]
    fn mesh_signal_matching_the_active_turn_is_applied() {
        let active: ActiveTurn =
            std::sync::Arc::new(std::sync::Mutex::new(Some("turn-1".to_string())));
        assert!(mesh_signal_applies_to_active_turn(&active, Some("turn-1")));
    }

    #[test]
    fn mesh_signal_for_a_stale_turn_is_rejected() {
        // The actual failure this guards: a resume delayed in the mesh for a
        // turn that has since genuinely stopped must not restore volume for
        // whatever is speaking now.
        let active: ActiveTurn =
            std::sync::Arc::new(std::sync::Mutex::new(Some("turn-2".to_string())));
        assert!(!mesh_signal_applies_to_active_turn(&active, Some("turn-1")));
    }

    #[test]
    fn mesh_signal_with_nothing_currently_active_is_applied() {
        let active: ActiveTurn = std::sync::Arc::new(std::sync::Mutex::new(None));
        assert!(mesh_signal_applies_to_active_turn(&active, Some("turn-1")));
    }

    #[test]
    fn audio_resume_deserializes_turn_id_when_present() {
        let resume: contracts::AudioResume =
            serde_json::from_str(r#"{"reason": "conflict_rejected", "turn_id": "turn-9"}"#)
                .unwrap();
        assert_eq!(resume.turn_id.as_deref(), Some("turn-9"));
    }

    #[test]
    fn audio_resume_defaults_turn_id_to_none_when_absent() {
        // A publisher that predates Phase 2D omits turn_id entirely -- must
        // still deserialize, unscoped (matching AudioStop's existing default).
        let resume: contracts::AudioResume = serde_json::from_str(r#"{}"#).unwrap();
        assert_eq!(resume.turn_id, None);
    }

    /// P4-9: a missing vocalization asset used to synthesize a sine/LCG-noise
    /// buzz -- audio in neither the cloned voice nor silence. It must now
    /// degrade to silence of the same nominal duration.
    #[test]
    fn missing_vocalization_asset_falls_back_to_silence_not_a_buzz() {
        let name = format!("definitely_missing_asset_{}", std::process::id());
        let pcm = load_vocalization_pcm(&name, 32_000);
        assert_eq!(pcm, contracts::silence_pcm(500, 32_000));
    }

    // ---------------------------------------------------------- Bucket 4: degenerate fragments

    #[test]
    fn degenerate_fragment_between_two_tokens_merges_backward_past_the_pause() {
        // A punctuation-only fragment can only become its own
        // TemporalPart::Text by being isolated between tokens in the first
        // place -- here "..." sits strictly between a <pause> and a
        // <hesitate>. It must fold into "hello"'s text (previous preferred)
        // rather than reach synthesis alone, while the Silence entry itself
        // stays exactly where it was -- only the neighbouring clause's
        // *content* changes, not the pause's position or duration.
        let parts = split_temporal_parts("hello<pause=20ms>...<hesitate>world").unwrap();
        assert_eq!(
            parts,
            vec![
                TemporalPart::Text("hello ...".to_string()),
                TemporalPart::Silence(20),
                TemporalPart::Hesitation(350),
                TemporalPart::Text("world".to_string()),
            ]
        );
    }

    #[test]
    fn degenerate_fragment_with_no_preceding_text_merges_forward() {
        // A leading punctuation-only fragment with nothing before it (only
        // a <hesitate> token) has no previous clause to fold into, so it
        // must attach to the next real text instead of reaching synthesis
        // on its own.
        let parts = split_temporal_parts("--<hesitate>hello").unwrap();
        assert_eq!(
            parts,
            vec![
                TemporalPart::Hesitation(350),
                TemporalPart::Text("-- hello".to_string()),
            ]
        );
    }

    #[test]
    fn all_degenerate_input_is_dropped_with_nothing_to_merge_into() {
        let parts = split_temporal_parts("... --").unwrap();
        assert_eq!(parts, Vec::<TemporalPart>::new());
    }

    #[test]
    fn timing_tags_become_silence_parts_not_text() {
        let parts = split_temporal_parts("hello<pause=20ms>there<hesitate>").unwrap();
        assert_eq!(
            parts,
            vec![
                TemporalPart::Text("hello".to_string()),
                TemporalPart::Silence(20),
                TemporalPart::Text("there".to_string()),
                TemporalPart::Hesitation(350),
            ]
        );
    }

    fn frame(t_ms: u32, rate: f64) -> contracts::ProsodyFrame {
        // Distinct, easily-traced values per frame: rate carries the frame's
        // identity, pitch/volume just need to differ from clamp_prosody's
        // defaults so a wrong-frame pick is visible.
        contracts::ProsodyFrame {
            time_offset_ms: t_ms,
            rate,
            pitch: 1.0 + rate * 0.01,
            volume: 0.5,
        }
    }

    /// P3-13: the whole point of the change. Before this, every chunk of a
    /// response used the same trajectory-wide average; now different points
    /// in time must read different frames.
    #[test]
    fn prosody_now_picks_the_frame_nearest_elapsed_time() {
        let frames: Vec<_> = (0..60)
            .map(|i| frame(i * 50, 1.0 + i as f64 * 0.01))
            .collect();
        let traj = ProsodyTrajectory {
            // Backdated so `elapsed()` reads a known, already-elapsed value
            // instead of a real-time sleep.
            received_at: std::time::Instant::now() - std::time::Duration::from_millis(500),
            frames,
        };

        let p = traj.prosody_now().unwrap();
        // 500ms in -> frame index 10 (t_ms = 500) -> rate = 1.0 + 10*0.01.
        assert!(
            (p.rate - 1.10).abs() < 1e-9,
            "expected the frame at ~500ms, got rate {}",
            p.rate
        );
    }

    /// Past the trajectory's own ~3s span, nearest-frame search must land on
    /// the last frame (the modeled steady-state tail) rather than panicking
    /// or picking the first one by default.
    #[test]
    fn prosody_now_past_the_trajectory_span_uses_the_last_frame() {
        let frames: Vec<_> = (0..60)
            .map(|i| frame(i * 50, 1.0 + i as f64 * 0.01))
            .collect();
        let traj = ProsodyTrajectory {
            received_at: std::time::Instant::now() - std::time::Duration::from_secs(30),
            frames,
        };

        let p = traj.prosody_now().unwrap();
        let last_rate = 1.0 + 59.0 * 0.01;
        assert!(
            (p.rate - last_rate).abs() < 1e-9,
            "expected the last frame's rate {last_rate}, got {}",
            p.rate
        );
    }

    #[test]
    fn prosody_now_is_none_for_an_empty_trajectory() {
        let traj = ProsodyTrajectory {
            received_at: std::time::Instant::now(),
            frames: vec![],
        };
        assert!(traj.prosody_now().is_none());
    }

    /// A trajectory received a moment ago (the onset of a fresh breath
    /// group, elapsed ~0ms) must read differently from one that has been
    /// playing for a while -- this is the drift itself, not just a lookup
    /// sanity check.
    #[test]
    fn two_points_in_the_same_trajectory_can_read_different_prosody() {
        let frames: Vec<_> = (0..60)
            .map(|i| frame(i * 50, 1.0 + i as f64 * 0.01))
            .collect();
        let early = ProsodyTrajectory {
            received_at: std::time::Instant::now(),
            frames: frames.clone(),
        };
        let later = ProsodyTrajectory {
            received_at: std::time::Instant::now() - std::time::Duration::from_millis(1000),
            frames,
        };

        assert_ne!(
            early.prosody_now().unwrap().rate,
            later.prosody_now().unwrap().rate
        );
    }

    #[test]
    fn latency_metadata_appends_voice_hop() {
        let event: ChatOutput = serde_json::from_str(include_str!(
            "../../contracts/fixtures/chat_output_chunk.json"
        ))
        .unwrap();
        let meta = build_latency_metadata(&event);
        let hops = meta["hops"].as_array().unwrap();

        assert_eq!(hops.last().unwrap()["agent"], "voice_agent");
        assert_eq!(hops.last().unwrap()["subject"], topics::AUDIO_STREAM);
    }

    /// P1-3: transport_agent scopes a confirmed audio.stop to the turn it
    /// names by reading this field back out of the X-Latency-Meta header --
    /// see `_on_nats_audio` / `_on_audio_stop` in transport_agent.py.
    #[test]
    fn latency_metadata_carries_the_current_events_turn_id() {
        let mut event: ChatOutput = serde_json::from_str(include_str!(
            "../../contracts/fixtures/chat_output_chunk.json"
        ))
        .unwrap();
        event.turn_id = Some("turn-abc".to_string());
        let meta = build_latency_metadata(&event);
        assert_eq!(meta["turn_id"], "turn-abc");
    }

    #[test]
    fn latency_metadata_overwrites_a_stale_inherited_turn_id() {
        let mut event: ChatOutput = serde_json::from_str(include_str!(
            "../../contracts/fixtures/chat_output_chunk.json"
        ))
        .unwrap();
        event.turn_id = Some("turn-new".to_string());
        event.latency_metadata = Some(contracts::LatencyMetadata {
            start_time: 0.0,
            hops: vec![],
            source: "stt_agent".to_string(),
            channels: None,
            sample_rate: None,
        });
        let meta = build_latency_metadata(&event);
        assert_eq!(meta["turn_id"], "turn-new");
    }

    /// P4-2: brain_agent computes where this chunk ends in the true response
    /// text and stamps it into `ChatOutput.metadata`; this process has no
    /// way to derive that itself (it only ever sees one chunk's `content`),
    /// so it must pass the values through unchanged rather than drop them.
    #[test]
    fn latency_metadata_passes_through_playback_progress_fields() {
        let mut event: ChatOutput = serde_json::from_str(include_str!(
            "../../contracts/fixtures/chat_output_chunk.json"
        ))
        .unwrap();
        event
            .metadata
            .insert("character_offset".to_string(), json!(42));
        event.metadata.insert("word_index".to_string(), json!(7));

        let meta = build_latency_metadata(&event);

        assert_eq!(meta["character_offset"], 42);
        assert_eq!(meta["word_index"], 7);
    }

    /// A chunk with no progress metadata (the exception-fallback path in
    /// brain_agent, which deliberately omits it) must not fabricate values.
    #[test]
    fn latency_metadata_omits_playback_progress_fields_when_absent() {
        let event: ChatOutput = serde_json::from_str(include_str!(
            "../../contracts/fixtures/chat_output_chunk.json"
        ))
        .unwrap();

        let meta = build_latency_metadata(&event);

        assert!(meta.get("character_offset").is_none());
        assert!(meta.get("word_index").is_none());
    }

    #[test]
    fn test_reverb_filter_processing() {
        // Bucket 2 (VOICE_REMEDIATION_PLAN.md): rewritten for the fixed echo (not
        // feedback) formula, y[n] = (x[n] + gain*x[n-D]) * headroom. headroom =
        // 1/(1+gain) = 1/1.5 = 0.6667 here, applied to the wet term even before any
        // delayed contribution exists -- see ReverbFilter::process's comment for why
        // that conservative tradeoff is deliberate (guarantees no clipping regardless
        // of content, at the cost of slightly attenuating single-sample-old signal).
        let mut filter = ReverbFilter::new(4, 0.5);
        let input_pcm = vec![10, 0, 20, 0, 30, 0, 40, 0, 50, 0, 60, 0];

        // 100% wet output
        let processed = filter.process(&input_pcm, 1.0);

        assert_eq!(processed.len(), input_pcm.len());
        let out_samples = processed
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        // Sample 0: delayed=0 (buffer starts empty). echoed = (10+0.5*0)*0.6667 = 6.667 -> 6.
        assert_eq!(out_samples[0], 6);
        // Sample 4 (index 4, buffer wrapped once): delayed = the ORIGINAL INPUT stored
        // at index 0 during sample 0 (10, not 55 -- storing input, not accumulated
        // output, is exactly this bucket's fix). echoed = (50 + 0.5*10)*0.6667 = 36.667 -> 36.
        assert_eq!(out_samples[4], 36);

        // Test 0% wet (completely dry output regardless of headroom, but state still
        // advances): the wet term's headroom scaling is multiplied by wet_gain=0, so
        // it contributes nothing and the dry path is untouched.
        let mut filter2 = ReverbFilter::new(4, 0.5);
        let processed2 = filter2.process(&input_pcm, 0.0);
        assert_eq!(processed2, input_pcm); // Exactly matches input
    }

    #[test]
    fn reverb_filter_preserves_samples_across_odd_chunks() {
        let mut filter = ReverbFilter::new(4, 0.5);

        let first = filter.process(&[10, 0, 20], 1.0);
        assert_eq!(first.len(), 2);
        // Sample 0 of this filter instance: same math as test_reverb_filter_processing
        // above -- (10+0)*0.6667 = 6.667 -> 6.
        assert_eq!(first, 6i16.to_le_bytes());

        let second = filter.process(&[0, 30, 0], 1.0);
        assert_eq!(second.len(), 4);
        let out_samples = second
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();
        // Samples 1 and 2 of this filter instance, delayed=0 for both (buffer
        // positions 1 and 2 have never been written): (20+0)*0.6667=13.33->13,
        // (30+0)*0.6667=20.0->20.
        assert_eq!(out_samples, vec![13, 20]);
    }

    #[test]
    fn reset_clears_the_pending_odd_byte_across_utterance_boundaries() {
        // Reviewer finding: reset() cleared the delay buffer and index but
        // left `pending_byte` untouched. A trailing odd byte held over from
        // one utterance's PCM would then splice onto the very first byte of
        // the NEXT, unrelated utterance -- the sibling PcmSampleFramer's
        // clear_history() already gets this right for the same kind of state.
        let mut filter = ReverbFilter::new(4, 0.5);

        // Leaves a dangling odd byte (value 20) after this call.
        let _ = filter.process(&[10, 0, 20], 1.0);
        filter.reset();

        // A fresh, unrelated utterance's first two bytes are a silent
        // sample. If the stale pending byte survived reset, it gets
        // prepended here, shifting this 2-byte input into a stale 3-byte
        // reassembly and producing a nonzero sample from data that belongs
        // to the utterance that just ended.
        let out = filter.process(&[0, 0], 1.0);
        let sample = i16::from_le_bytes([out[0], out[1]]);
        assert_eq!(
            sample, 0,
            "a byte from the previous utterance leaked across reset(): got {sample}, want 0"
        );
    }

    #[test]
    fn reverb_feedback_does_not_run_away_on_sustained_input() {
        // Bucket 2: the actual bug this whole fix targets. The old code wrote
        // `output` (not `input`) into the delay line, making it true feedback
        // (y[n] = x[n] + gain*y[n-D]), which compounds by (1+gain) every full pass
        // around the D-slot delay line -- at gain=0.5 that is 1.5x per cycle, so a
        // sustained tone diverges without bound. Checking `sample <= i16::MAX` on the
        // OUTPUT cannot catch this: `clamp` unconditionally forces every i16 into
        // range regardless of how far the underlying signal overshot, so a badly
        // clipped, flat-topped, harshly distorted waveform is indistinguishable from
        // a correct one by that check alone -- every sample trivially satisfies it.
        //
        // What actually distinguishes the two: a bounded *echo* (this fix) converges
        // to unity gain in steady state -- (x + gain*x)*headroom = x*(1+gain)/(1+gain)
        // = x exactly, converging back to the ORIGINAL input amplitude. Unbounded
        // *feedback* does not converge at all; it keeps growing every cycle. Using an
        // input well under full scale (16000, not i16::MAX) makes that growth
        // observable as a value clearly higher than the input, rather than being
        // masked by clamp's ceiling before the difference is visible.
        const INPUT_AMPLITUDE: i16 = 16_000;
        let mut filter = ReverbFilter::new(4, 0.5);
        let sustained_tone: Vec<u8> = (0..40) // 10 full cycles around the 4-slot delay line
            .flat_map(|_| INPUT_AMPLITUDE.to_le_bytes())
            .collect();

        let processed = filter.process(&sustained_tone, 1.0);
        let out_samples = processed
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        let steady_state = *out_samples.last().unwrap();
        assert!(
            (steady_state - INPUT_AMPLITUDE).abs() <= 2,
            "expected convergence to the input amplitude ({INPUT_AMPLITUDE}) within \
             rounding, got {steady_state} -- feedback is compounding instead of a \
             bounded echo converging"
        );
    }

    #[test]
    fn reverb_reset_clears_the_delay_line() {
        // Bucket 2: before this, ReverbFilter was constructed once per process and
        // never reset at all, so a reverb tail could bleed from one utterance into a
        // completely unrelated later one. reset() must actually zero the buffer, not
        // just exist as a no-op.
        let mut filter = ReverbFilter::new(4, 0.5);
        filter.process(&[10, 0, 20, 0, 30, 0, 40, 0, 50, 0], 1.0);

        filter.reset();

        // A fresh filter and a reset one must behave identically on the same input.
        let mut fresh = ReverbFilter::new(4, 0.5);
        let from_reset = filter.process(&[7, 0, 9, 0], 1.0);
        let from_fresh = fresh.process(&[7, 0, 9, 0], 1.0);
        assert_eq!(from_reset, from_fresh);
    }

    #[test]
    fn pcm_sample_framer_does_not_blend_consecutive_chunks() {
        // Already emitted audio must never be replayed into the next chunk.
        let mut filter = PcmSampleFramer::new();

        let chunk1_bytes: Vec<u8> = vec![100_i16; 600]
            .into_iter()
            .flat_map(|s| s.to_le_bytes())
            .collect();
        let out1 = filter.process(&chunk1_bytes);
        assert_eq!(out1, chunk1_bytes);

        let chunk2_bytes: Vec<u8> = vec![200_i16; 600]
            .into_iter()
            .flat_map(|s| s.to_le_bytes())
            .collect();
        let out2 = filter.process(&chunk2_bytes);

        // The whole point: chunk2 comes through byte-for-byte identical, not blended
        // with chunk1's tail. No sample anywhere is a value neither chunk contains.
        assert_eq!(out2, chunk2_bytes);
        let out2_samples = out2
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();
        assert!(out2_samples.iter().all(|&s| s == 200));
    }

    #[test]
    fn pcm_sample_framer_preserves_a_split_sample() {
        // The one thing this type still legitimately does: an odd trailing byte from
        // one chunk must combine with the next chunk's first byte into a whole 16-bit
        // sample, not get silently dropped or misaligned.
        let mut filter = PcmSampleFramer::new();

        let first = filter.process(&[0x10]);
        assert!(first.is_empty());

        let second = filter.process(&[0x20, 0x00, 0x00]);
        // Buffered 0x10 prepended to [0x20, 0x00, 0x00] makes 4 bytes -- already
        // even, so nothing is held back this time: all four come through as two
        // whole samples, the buffered byte correctly forming the low byte of the
        // first one.
        assert_eq!(second, vec![0x10, 0x20, 0x00, 0x00]);
    }

    #[test]
    fn test_vocal_gain_scaling() {
        // Test that noise scaling correctly shifts sample amplitudes
        let input_pcm = vec![1000_i16, -2000, 3000];
        let mut bytes = Vec::new();
        for &s in &input_pcm {
            bytes.extend_from_slice(&s.to_le_bytes());
        }

        // Test scaling down (quiet environment, multiplier < 1.0)
        let mut scale_down_bytes = bytes.clone();
        scale_pcm_in_place(&mut scale_down_bytes, 0.7);

        let scaled_down_samples = scale_down_bytes
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        assert_eq!(scaled_down_samples[0], 700);
        assert_eq!(scaled_down_samples[1], -1400);
        assert_eq!(scaled_down_samples[2], 2100);

        // Test scaling up (noisy environment, multiplier > 1.0)
        let mut scale_up_bytes = bytes.clone();
        scale_pcm_in_place(&mut scale_up_bytes, 1.4);

        let scaled_up_samples = scale_up_bytes
            .chunks_exact(2)
            .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
            .collect::<Vec<i16>>();

        assert_eq!(scaled_up_samples[0], 1400);
        assert_eq!(scaled_up_samples[1], -2800);
        assert_eq!(scaled_up_samples[2], 4200);
    }

    #[test]
    fn utterance_gain_combines_volume_and_noise() {
        let noise = std::sync::Mutex::new(1.2f64);
        assert!((utterance_gain(&noise, 0.5) - 0.6).abs() < 1e-9);
    }

    #[test]
    fn utterance_gain_falls_back_when_lock_poisoned() {
        let noise = std::sync::Arc::new(std::sync::Mutex::new(1.2f64));
        let n2 = noise.clone();
        let _ = std::thread::spawn(move || {
            let _g = n2.lock().unwrap();
            panic!("poison");
        })
        .join();
        // Poisoned lock must degrade to unity noise compensation, not silence.
        assert!((utterance_gain(&noise, 0.5) - 0.5).abs() < 1e-9);
    }

    // ---------------------------------------------------------- select_emotion_bucket

    fn affect(valence: f64, arousal: f64) -> contracts::ChatOutputAffect {
        contracts::ChatOutputAffect {
            valence,
            arousal,
            ..Default::default()
        }
    }

    #[test]
    fn no_affect_selects_neutral() {
        assert_eq!(select_emotion_bucket(None, None), EmotionBucket::Neutral);
    }

    #[test]
    fn high_valence_high_arousal_is_excited() {
        let a = affect(0.5, 0.8);
        assert_eq!(
            select_emotion_bucket(Some(&a), None),
            EmotionBucket::Excited
        );
    }

    #[test]
    fn high_valence_low_arousal_is_warm_not_excited() {
        let a = affect(0.5, 0.3);
        assert_eq!(select_emotion_bucket(Some(&a), None), EmotionBucket::Warm);
    }

    #[test]
    fn low_valence_high_arousal_is_concerned() {
        let a = affect(-0.5, 0.7);
        assert_eq!(
            select_emotion_bucket(Some(&a), None),
            EmotionBucket::Concerned
        );
    }

    #[test]
    fn low_valence_low_arousal_is_neutral_not_concerned() {
        // Negative but not aroused reads as flat, not distressed -- concern
        // needs both the valence and the arousal signal, not valence alone.
        let a = affect(-0.5, 0.2);
        assert_eq!(
            select_emotion_bucket(Some(&a), None),
            EmotionBucket::Neutral
        );
    }

    #[test]
    fn near_zero_valence_low_arousal_is_calm() {
        let a = affect(0.0, 0.1);
        assert_eq!(select_emotion_bucket(Some(&a), None), EmotionBucket::Calm);
    }

    #[test]
    fn near_zero_valence_high_arousal_is_neutral_not_calm() {
        // Aroused-but-neither-good-nor-bad (e.g. startled, alert) must not
        // read as the same settled register as truly low-arousal calm.
        let a = affect(0.0, 0.9);
        assert_eq!(
            select_emotion_bucket(Some(&a), None),
            EmotionBucket::Neutral
        );
    }

    #[test]
    fn deadband_boundary_is_exclusive_not_inclusive() {
        // Exactly at the deadband edge, with arousal high enough to reach
        // Excited if the edge were inclusive: must land on Neutral, not
        // Excited. A low-arousal probe here would pass even if `>` regressed
        // to `>=`, since it would still fall through to Calm either way --
        // this specifically exercises the branch the boundary guards.
        let at_edge = affect(0.15, 0.65);
        assert_eq!(
            select_emotion_bucket(Some(&at_edge), None),
            EmotionBucket::Neutral
        );
    }

    // ---------------------------------------------- select_emotion_bucket (expression, Phase 3C)

    fn expression(affect_label: &str) -> contracts::SpeechExpressionWire {
        contracts::SpeechExpressionWire {
            affect_label: Some(affect_label.to_string()),
            ..Default::default()
        }
    }

    #[test]
    fn expression_affect_label_overrides_disagreeing_affect() {
        // Affect alone reads Excited (valence 0.5, arousal 0.8); a Phase 3A
        // producer naming the register directly must win over re-deriving
        // it from the raw PAD values -- the additive-lookup contract.
        let a = affect(0.5, 0.8);
        let e = expression("calm");
        assert_eq!(
            select_emotion_bucket(Some(&a), Some(&e)),
            EmotionBucket::Calm
        );
    }

    #[test]
    fn expression_affect_label_is_case_insensitive() {
        let e = expression("WARM");
        assert_eq!(select_emotion_bucket(None, Some(&e)), EmotionBucket::Warm);
    }

    #[test]
    fn expression_with_unrecognized_affect_label_falls_back_to_affect() {
        let a = affect(0.5, 0.8);
        let e = expression("bittersweet");
        assert_eq!(
            select_emotion_bucket(Some(&a), Some(&e)),
            EmotionBucket::Excited
        );
    }

    #[test]
    fn expression_present_but_affect_label_unset_falls_back_to_affect() {
        // A `SpeechExpressionWire` at its wire defaults (`affect_label:
        // None`, `style: "neutral"`) must not force Neutral over a real
        // affect-based read -- see `emotion_bucket_from_expression`'s own
        // doc comment on why `style`'s default is not consulted here.
        let a = affect(0.5, 0.8);
        let e = contracts::SpeechExpressionWire::default();
        assert_eq!(
            select_emotion_bucket(Some(&a), Some(&e)),
            EmotionBucket::Excited
        );
    }

    // ---------------------------------------------- temporal_parts_from_expression (Phase 3C)

    #[test]
    fn expression_below_both_floors_yields_plain_text() {
        let e = contracts::SpeechExpressionWire {
            hesitation: 0.1,
            breath: 0.1,
            ..Default::default()
        };
        assert_eq!(
            temporal_parts_from_expression("hello friend", &e),
            vec![TemporalPart::Text("hello friend".to_string())]
        );
    }

    #[test]
    fn expression_hesitation_above_floor_prepends_hesitation() {
        let e = contracts::SpeechExpressionWire {
            hesitation: 1.0,
            breath: 0.0,
            ..Default::default()
        };
        assert_eq!(
            temporal_parts_from_expression("hello friend", &e),
            vec![
                TemporalPart::Hesitation(EXPRESSION_HESITATION_MAX_MS),
                TemporalPart::Text("hello friend".to_string()),
            ]
        );
    }

    // Stage 3 (Blocker 2): Codex 3A's `_breath_level` encodes the legacy
    // `<sigh_soft>` case (low arousal, negative valence) at `breath == 0.5`
    // and the legacy `<breath_fast>` case (high arousal, negative valence)
    // at `breath == 1.0` -- these two must land on different vocalizations,
    // not collapse to the same one.
    #[test]
    fn expression_breath_at_legacy_sigh_soft_level_prepends_sigh_soft() {
        let e = contracts::SpeechExpressionWire {
            hesitation: 0.0,
            breath: 0.5,
            ..Default::default()
        };
        assert_eq!(
            temporal_parts_from_expression("hello friend", &e),
            vec![
                TemporalPart::Vocalization("sigh_soft".to_string()),
                TemporalPart::Text("hello friend".to_string()),
            ]
        );
    }

    #[test]
    fn expression_breath_at_legacy_breath_fast_level_prepends_breath_fast() {
        let e = contracts::SpeechExpressionWire {
            hesitation: 0.0,
            breath: 1.0,
            ..Default::default()
        };
        assert_eq!(
            temporal_parts_from_expression("hello friend", &e),
            vec![
                TemporalPart::Vocalization("breath_fast".to_string()),
                TemporalPart::Text("hello friend".to_string()),
            ]
        );
    }

    #[test]
    fn expression_breath_band_boundaries_are_inclusive_low_exclusive_high() {
        // 0.25: enters the sigh_soft band. Just below 0.75: still sigh_soft,
        // not breath_fast. 0.75 exactly: crosses into breath_fast. A `>`
        // regressing to `>=` (or vice versa) on either boundary would pass
        // some of these but not all four.
        let sigh_at_floor = contracts::SpeechExpressionWire {
            breath: 0.25,
            ..Default::default()
        };
        let sigh_just_under_fast = contracts::SpeechExpressionWire {
            breath: 0.749,
            ..Default::default()
        };
        let fast_at_floor = contracts::SpeechExpressionWire {
            breath: 0.75,
            ..Default::default()
        };
        let below_soft_floor = contracts::SpeechExpressionWire {
            breath: 0.249,
            ..Default::default()
        };

        assert_eq!(
            temporal_parts_from_expression("x", &sigh_at_floor),
            vec![
                TemporalPart::Vocalization("sigh_soft".to_string()),
                TemporalPart::Text("x".to_string())
            ]
        );
        assert_eq!(
            temporal_parts_from_expression("x", &sigh_just_under_fast),
            vec![
                TemporalPart::Vocalization("sigh_soft".to_string()),
                TemporalPart::Text("x".to_string())
            ]
        );
        assert_eq!(
            temporal_parts_from_expression("x", &fast_at_floor),
            vec![
                TemporalPart::Vocalization("breath_fast".to_string()),
                TemporalPart::Text("x".to_string())
            ]
        );
        assert_eq!(
            temporal_parts_from_expression("x", &below_soft_floor),
            vec![TemporalPart::Text("x".to_string())]
        );
    }

    #[test]
    fn expression_does_not_reparse_legacy_tags_in_content() {
        // A literal `<hesitate>` surviving in `content` alongside a
        // populated `expression` must not be re-parsed as a second,
        // duplicate hesitation -- the structured channel is authoritative
        // once present, and `content` is plain text on this path.
        let e = contracts::SpeechExpressionWire::default();
        assert_eq!(
            temporal_parts_from_expression("hello<hesitate>friend", &e),
            vec![TemporalPart::Text("hello<hesitate>friend".to_string())]
        );
    }

    // ---------------------------------------------------------- EmotionRefSet

    fn clip(tag: &str) -> RefClip {
        RefClip {
            audio_path: format!("output/{tag}.wav"),
            text: format!("{tag} reference transcript"),
        }
    }

    #[test]
    fn unconfigured_buckets_fall_back_to_neutral() {
        let set = EmotionRefSet {
            neutral: clip("neutral"),
            calm: None,
            warm: None,
            concerned: None,
            excited: None,
        };
        assert_eq!(set.resolve(EmotionBucket::Calm), &clip("neutral"));
        assert_eq!(set.resolve(EmotionBucket::Warm), &clip("neutral"));
        assert_eq!(set.resolve(EmotionBucket::Concerned), &clip("neutral"));
        assert_eq!(set.resolve(EmotionBucket::Excited), &clip("neutral"));
    }

    #[test]
    fn configured_bucket_resolves_to_itself_not_neutral() {
        let set = EmotionRefSet {
            neutral: clip("neutral"),
            calm: None,
            warm: Some(clip("warm")),
            concerned: None,
            excited: None,
        };
        assert_eq!(set.resolve(EmotionBucket::Warm), &clip("warm"));
        // Sibling buckets stay on neutral -- configuring one must not leak
        // into the others.
        assert_eq!(set.resolve(EmotionBucket::Calm), &clip("neutral"));
    }

    // ---------------------------------------------------------- optional_ref_clip
    //
    // These mutate process env vars, like the STT crate's existing
    // `backend_defaults_to_whisper_not_mock` does. Var names are unique to
    // this feature so they cannot collide with another test in this binary.

    #[test]
    fn missing_env_pair_yields_none() {
        std::env::remove_var("REF_AUDIO_PATH_TESTBUCKETA");
        std::env::remove_var("REF_TEXT_TESTBUCKETA");
        assert_eq!(optional_ref_clip("TESTBUCKETA"), None);
    }

    #[test]
    fn audio_without_text_yields_none_not_a_mismatched_pair() {
        std::env::set_var("REF_AUDIO_PATH_TESTBUCKETB", "output/warm.wav");
        std::env::remove_var("REF_TEXT_TESTBUCKETB");
        assert_eq!(optional_ref_clip("TESTBUCKETB"), None);
        std::env::remove_var("REF_AUDIO_PATH_TESTBUCKETB");
    }

    #[test]
    fn both_vars_present_yields_the_clip() {
        std::env::set_var("REF_AUDIO_PATH_TESTBUCKETC", "output/warm.wav");
        std::env::set_var("REF_TEXT_TESTBUCKETC", "a warm greeting");
        assert_eq!(
            optional_ref_clip("TESTBUCKETC"),
            Some(RefClip {
                audio_path: "output/warm.wav".to_string(),
                text: "a warm greeting".to_string(),
            })
        );
        std::env::remove_var("REF_AUDIO_PATH_TESTBUCKETC");
        std::env::remove_var("REF_TEXT_TESTBUCKETC");
    }

    // ---------------------------------------------------- reference_clip_missing
    //
    // Before this check existed, a missing reference clip produced no
    // symptom anywhere in voice-agent's own logs -- just a healthcheck
    // failing elsewhere with nothing pointing back at the cause.

    #[test]
    fn reports_missing_for_a_path_that_does_not_exist() {
        assert!(reference_clip_missing(
            "/definitely/does/not/exist/clip.wav"
        ));
    }

    #[test]
    fn reports_present_for_a_path_that_exists() {
        let path =
            std::env::temp_dir().join(format!("voice_agent_test_clip_{}.wav", std::process::id()));
        std::fs::write(&path, b"fake-clip-bytes").unwrap();

        assert!(!reference_clip_missing(path.to_str().unwrap()));

        std::fs::remove_file(&path).unwrap();
    }

    // ---------------------------------------------------------- CircuitBreaker

    #[test]
    fn breaker_starts_closed() {
        let cb = CircuitBreaker::new(3, 15_000);
        assert!(cb.allow_request(0));
        assert!(!cb.is_open(0));
    }

    #[test]
    fn breaker_does_not_open_before_threshold() {
        let cb = CircuitBreaker::new(3, 15_000);
        cb.record_failure(100);
        cb.record_failure(200);
        assert!(
            !cb.is_open(200),
            "two failures under a threshold of three must not open the breaker"
        );
    }

    #[test]
    fn breaker_opens_exactly_at_threshold() {
        let cb = CircuitBreaker::new(3, 15_000);
        cb.record_failure(100);
        cb.record_failure(200);
        cb.record_failure(300);
        assert!(cb.is_open(300));
    }

    #[test]
    fn breaker_stays_open_within_cooldown() {
        let cb = CircuitBreaker::new(1, 15_000);
        cb.record_failure(1_000);
        assert!(cb.is_open(1_000));
        assert!(cb.is_open(1_000 + 14_999));
    }

    #[test]
    fn breaker_allows_half_open_trial_once_cooldown_elapses() {
        let cb = CircuitBreaker::new(1, 15_000);
        cb.record_failure(1_000);
        assert!(
            cb.allow_request(1_000 + 15_000),
            "cooldown elapsed must permit exactly one half-open trial"
        );
    }

    #[test]
    fn success_fully_resets_the_breaker() {
        let cb = CircuitBreaker::new(1, 15_000);
        cb.record_failure(100);
        assert!(cb.is_open(100));
        cb.record_success();
        assert!(
            !cb.is_open(100),
            "success must close an already-open breaker, not just stop \
             counting toward the next opening"
        );
    }

    #[test]
    fn success_after_a_near_miss_does_not_leave_a_head_start() {
        let cb = CircuitBreaker::new(3, 15_000);
        cb.record_failure(100);
        cb.record_failure(200);
        cb.record_success();
        // A prior near-miss must not carry over: it takes a fresh full
        // threshold of failures to open again, not just one more.
        cb.record_failure(300);
        assert!(!cb.is_open(300));
    }

    #[test]
    fn failed_half_open_trial_rearms_the_cooldown() {
        let cb = CircuitBreaker::new(1, 15_000);
        cb.record_failure(1_000); // opens at t=1000, cooldown until t=16000
        let trial_time = 1_000 + 15_000; // t=16000, half-open trial begins
        assert!(cb.allow_request(trial_time));
        cb.record_failure(trial_time); // trial failed: re-arm from t=16000
        assert!(
            cb.is_open(trial_time + 14_999),
            "a failed half-open trial must restart the cooldown from its own \
             timestamp, not leave the original one in place"
        );
        assert!(cb.allow_request(trial_time + 15_000));
    }

    // ---------------------------------------------------------- synthesize_stream_with_retry
    // These hit a real local HTTP mock (wiremock), not the network — no live
    // model or external service is contacted.

    fn test_voice_config(sovits_url: String) -> VoiceConfig {
        VoiceConfig {
            nats_url: "nats://127.0.0.1:4222".to_string(),
            sovits_url,
            emotion_refs: EmotionRefSet {
                neutral: clip("neutral"),
                calm: None,
                warm: None,
                concerned: None,
                excited: None,
            },
            tts_language: "en".to_string(),
            sample_rate: 32_000,
        }
    }

    #[tokio::test]
    async fn retry_recovers_from_transient_failures() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        // First two calls fail; the third (last allowed attempt) succeeds --
        // proves retries actually happen and a late success is not treated as
        // a failure just because earlier attempts were.
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(500))
            .up_to_n_times(2)
            .mount(&server)
            .await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![1, 2, 3]))
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let result = synthesize_stream_with_retry(
            &config,
            &http,
            "hello",
            &config.emotion_refs.neutral,
            1.0,
            1.0,
            1.0,
        )
        .await;
        assert!(
            result.is_ok(),
            "a transient failure within the retry budget must eventually succeed"
        );
    }

    #[tokio::test]
    async fn retry_gives_up_after_max_attempts() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(500))
            .expect(MAX_SYNTHESIS_ATTEMPTS as u64)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let result = synthesize_stream_with_retry(
            &config,
            &http,
            "hello",
            &config.emotion_refs.neutral,
            1.0,
            1.0,
            1.0,
        )
        .await;
        assert!(
            result.is_err(),
            "a server that never recovers must exhaust the retry budget and fail"
        );
        // `.expect(N)` above is itself verified when `server` drops at end of
        // scope -- an unexpected attempt count fails the test.
    }

    #[tokio::test]
    async fn validation_rejection_is_not_retried() {
        // Bucket 4 (VOICE_REMEDIATION_PLAN.md): GPT-SoVITS answers a
        // degenerate fragment with 400 + "Please enter valid text." --
        // deterministic, so retrying it is pure waste. `.expect(1)` fails
        // the test the moment a second attempt is made.
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(400).set_body_string("Please enter valid text."))
            .expect(1)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let result = synthesize_stream_with_retry(
            &config,
            &http,
            "...",
            &config.emotion_refs.neutral,
            1.0,
            1.0,
            1.0,
        )
        .await;
        assert!(
            result.is_err(),
            "a validation rejection must still surface as an error to the caller"
        );
        let err = result.unwrap_err();
        assert!(
            err.downcast_ref::<SynthesisRejected>().is_some(),
            "the error must be identifiable as a rejection, not a generic failure, \
             so the caller can skip counting it against the circuit breaker: {err:#}"
        );
        // `.expect(1)` above is the real assertion: it fails when the server
        // drops at end of scope if more than one request was ever made.
    }

    #[tokio::test]
    async fn a_generic_server_error_is_not_mistaken_for_a_validation_rejection() {
        // Guards the other direction of the same distinction: a 400 that
        // does NOT carry the validation phrasing must still retry like any
        // other failure, not be silently treated as an unspeakable fragment.
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(400).set_body_string("internal error"))
            .expect(MAX_SYNTHESIS_ATTEMPTS as u64)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let result = synthesize_stream_with_retry(
            &config,
            &http,
            "hello",
            &config.emotion_refs.neutral,
            1.0,
            1.0,
            1.0,
        )
        .await;
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.downcast_ref::<SynthesisRejected>().is_none(),
            "a 400 without the validation phrasing must not be classified as a rejection"
        );
        // `.expect(N)` above proves the retry budget was actually used.
    }

    // ---------------------------------------------------------- probe_synthesis

    #[tokio::test]
    async fn probe_fails_on_empty_response_body_not_just_bad_status() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        // 200 OK but zero bytes: the exact "up but broken" failure mode a
        // plain status/health ping would miss.
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200))
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let result = probe_synthesis(&config, &http).await;
        assert!(
            result.is_err(),
            "a 200 with an empty body must not be reported as healthy"
        );
    }

    #[tokio::test]
    async fn probe_succeeds_when_real_audio_bytes_come_back() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![0, 1, 2, 3]))
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        assert!(probe_synthesis(&config, &http).await.is_ok());
    }

    // ---------------------------------------------------------- hesitation_pcm

    fn empty_hesitation_cache() -> HesitationCache {
        std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()))
    }

    fn test_prosody() -> contracts::Prosody {
        contracts::Prosody {
            rate: 1.0,
            pitch: 1.0,
            volume: 1.0,
            pause_bias: 1.0,
        }
    }

    /// P4-9: an open breaker must skip the network entirely and return
    /// silence -- never the old sine/noise buzz, and never a network call
    /// against a known-down engine. `.expect(0)` on the mock fails the test
    /// if a request happens anyway.
    #[tokio::test]
    async fn hesitation_pcm_returns_silence_when_breaker_is_open() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![9, 9, 9, 9]))
            .expect(0)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let breaker = CircuitBreaker::new(1, 60_000);
        breaker.record_failure(now_millis());
        assert!(
            breaker.is_open(now_millis()),
            "precondition: breaker must be open"
        );
        let cache = empty_hesitation_cache();

        let pcm = hesitation_pcm(
            &config,
            &http,
            &breaker,
            &cache,
            EmotionBucket::Neutral,
            &config.emotion_refs.neutral,
            350,
            config.sample_rate,
            &test_prosody(),
        )
        .await;

        assert_eq!(pcm, contracts::silence_pcm(350, config.sample_rate));
    }

    /// A cache hit must return the cached audio without touching the
    /// network at all, regardless of breaker state.
    #[tokio::test]
    async fn hesitation_pcm_returns_cached_bytes_without_a_network_call() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![9, 9, 9, 9]))
            .expect(0)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let breaker = CircuitBreaker::new(3, 60_000);
        let cache = empty_hesitation_cache();
        let cached_bytes = vec![7, 7, 7, 7];
        cache
            .lock()
            .await
            .insert(EmotionBucket::Neutral, cached_bytes.clone());

        let pcm = hesitation_pcm(
            &config,
            &http,
            &breaker,
            &cache,
            EmotionBucket::Neutral,
            &config.emotion_refs.neutral,
            350,
            config.sample_rate,
            &test_prosody(),
        )
        .await;

        assert_eq!(pcm, cached_bytes);
    }

    /// The success path: a real synthesis call must both return the
    /// synthesized audio and populate the cache for the next call.
    #[tokio::test]
    async fn hesitation_pcm_synthesizes_and_caches_on_success() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        let synthesized = vec![1, 2, 3, 4, 5, 6];
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(synthesized.clone()))
            .expect(1)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let breaker = CircuitBreaker::new(3, 60_000);
        let cache = empty_hesitation_cache();

        let pcm = hesitation_pcm(
            &config,
            &http,
            &breaker,
            &cache,
            EmotionBucket::Neutral,
            &config.emotion_refs.neutral,
            350,
            config.sample_rate,
            &test_prosody(),
        )
        .await;

        assert_eq!(pcm, synthesized);
        assert_eq!(
            cache.lock().await.get(&EmotionBucket::Neutral),
            Some(&synthesized),
            "a successful synthesis must be cached for the next hesitation"
        );
        assert!(
            !breaker.is_open(now_millis()),
            "a success must not leave the breaker open"
        );
    }

    /// Synthesis failing (engine reachable but erroring, within the retry
    /// budget) must fall back to silence, not a buzz, and must record onto
    /// the shared breaker so a real outage still trips it.
    #[tokio::test]
    async fn hesitation_pcm_falls_back_to_silence_on_synthesis_failure() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(500))
            .expect(MAX_SYNTHESIS_ATTEMPTS as u64)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let breaker = CircuitBreaker::new(1, 60_000);
        let cache = empty_hesitation_cache();

        let pcm = hesitation_pcm(
            &config,
            &http,
            &breaker,
            &cache,
            EmotionBucket::Neutral,
            &config.emotion_refs.neutral,
            350,
            config.sample_rate,
            &test_prosody(),
        )
        .await;

        assert_eq!(pcm, contracts::silence_pcm(350, config.sample_rate));
        assert!(
            breaker.is_open(now_millis()),
            "a real synthesis failure must count toward the shared breaker"
        );
        assert!(
            cache.lock().await.get(&EmotionBucket::Neutral).is_none(),
            "a failed attempt must not be cached"
        );
    }

    /// Reviewer finding: a rejected filler (GPT-SoVITS's deterministic
    /// "Please enter valid text." 400) must fall back to silence WITHOUT
    /// counting toward the shared breaker -- a validation rejection says
    /// nothing about whether the engine is up, and the main synthesis call
    /// site already treats it this way (see the `SynthesisRejected` arm
    /// where the real per-chunk synthesis happens). Also must not be
    /// retried: `.expect(1)` fails the test if more than one request went
    /// out, matching `validation_rejection_is_not_retried` below for the
    /// main synthesis path.
    #[tokio::test]
    async fn hesitation_pcm_rejection_falls_back_to_silence_without_opening_the_breaker() {
        use wiremock::matchers::{method, path};
        use wiremock::{Mock, MockServer, ResponseTemplate};

        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/tts"))
            .respond_with(ResponseTemplate::new(400).set_body_string("Please enter valid text."))
            .expect(1)
            .mount(&server)
            .await;

        let config = test_voice_config(server.uri());
        let http = Client::new();
        let breaker = CircuitBreaker::new(1, 60_000);
        let cache = empty_hesitation_cache();

        let pcm = hesitation_pcm(
            &config,
            &http,
            &breaker,
            &cache,
            EmotionBucket::Neutral,
            &config.emotion_refs.neutral,
            350,
            config.sample_rate,
            &test_prosody(),
        )
        .await;

        assert_eq!(pcm, contracts::silence_pcm(350, config.sample_rate));
        assert!(
            !breaker.is_open(now_millis()),
            "a validation rejection is not an engine outage and must not open the breaker"
        );
        assert!(
            cache.lock().await.get(&EmotionBucket::Neutral).is_none(),
            "a rejected fragment must not be cached as if it were valid audio"
        );
    }

    const STREAM: &str = "P2_2_PUBLISH_PCM_TEST";

    /// P2-2: `publish_pcm` must not wait for JetStream's publish ack.
    ///
    /// Needs a live NATS, and skips loudly without one -- same shape as
    /// stt-agent's `real_model_loads_and_perceives_audio`, which skips when the
    /// model is unprovisioned. Also skips if a stream already covers
    /// `audio.stream`, so it can never disturb a provisioned `AI_AUDIO`.
    ///
    /// The threshold is measured in-test rather than hardcoded, because the ack
    /// round-trip depends entirely on where NATS is: on loopback it is a
    /// fraction of a millisecond, and across the machine boundary the
    /// robot-plus-server split implies, far more. Calibrating against a real
    /// round-trip taken moments earlier keeps the assertion meaningful on both.
    #[tokio::test]
    async fn publish_pcm_does_not_wait_for_the_jetstream_ack() {
        let url = std::env::var("NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".to_string());
        let Ok(client) = async_nats::connect(&url).await else {
            eprintln!("SKIP: no NATS at {url}");
            return;
        };
        let js = async_nats::jetstream::new(client);

        if js.get_stream(STREAM).await.is_ok() || js.get_stream("AI_AUDIO").await.is_ok() {
            eprintln!("SKIP: a stream already covers audio.stream; refusing to touch it");
            return;
        }

        js.create_stream(async_nats::jetstream::stream::Config {
            name: STREAM.to_string(),
            subjects: vec![topics::AUDIO_STREAM.to_string()],
            storage: async_nats::jetstream::stream::StorageType::Memory,
            ..Default::default()
        })
        .await
        .expect("create the test stream");

        let event = ChatOutput {
            content: None,
            done: false,
            turn_id: Some("p2-2-test".to_string()),
            affect: None,
            expression: None,
            timestamp: 0.0,
            full_response: None,
            generation_error: None,
            proactive: false,
            metadata: Default::default(),
            latency_metadata: None,
        };
        let chunk = || vec![0u8; 640]; // ~20ms of 16-bit PCM at 16 kHz

        // Calibrate: one publish whose ack we *do* wait for.
        let started = std::time::Instant::now();
        js.publish(topics::AUDIO_STREAM, Bytes::from(chunk()))
            .await
            .expect("publish")
            .await
            .expect("ack");
        let ack_rtt = started.elapsed();

        const N: usize = 200;
        let started = std::time::Instant::now();
        for _ in 0..N {
            publish_pcm(&js, chunk(), &event, 1.0)
                .await
                .expect("publish_pcm must succeed");
        }
        let elapsed = started.elapsed();

        // Awaiting the ack per chunk costs at least N round-trips. A quarter of
        // that is a wide margin: the point is the difference in kind, not a
        // tuned number.
        let serialized = ack_rtt * (N as u32);
        assert!(
            elapsed < serialized / 4,
            "publish_pcm looks like it is still waiting for acks: {N} chunks took \
             {elapsed:?}, and one ack round-trip alone is {ack_rtt:?} \
             (serialized would be ~{serialized:?})"
        );

        // Not waiting must not mean not delivering. This is the half that
        // matters: outbound PCM is the agent's actual speech, not the ephemeral
        // observability sample the inbound side drops acks on.
        let info = js
            .get_stream(STREAM)
            .await
            .expect("stream")
            .info()
            .await
            .expect("stream info")
            .state
            .messages;
        let _ = js.delete_stream(STREAM).await;
        assert_eq!(
            info,
            (N + 1) as u64,
            "chunks went missing once the ack stopped being awaited"
        );
    }

    // ---------------------------------------------------------- P2-1: connect_nats

    /// Kills the spawned nats-server on drop, including on test panic, so a
    /// failed assertion never leaves an orphaned server bound to the port.
    struct NatsServerGuard(std::process::Child);

    impl Drop for NatsServerGuard {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    fn free_port() -> u16 {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind ephemeral port");
        listener.local_addr().expect("local addr").port()
    }

    /// Spawns a real nats-server from the actual shipped `nats-accounts.conf`
    /// (same file Python's `test_nats_accounts_enforcement.py` boots), or
    /// returns `None` (skip) if the binary is not on PATH.
    async fn spawn_accounts_server() -> Option<(NatsServerGuard, u16)> {
        if std::process::Command::new("nats-server")
            .arg("--version")
            .output()
            .is_err()
        {
            eprintln!("SKIP: no nats-server binary on PATH -- install it to run these");
            return None;
        }

        let conf = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../nats-accounts.conf");
        let port = free_port();
        let store_dir = std::env::temp_dir().join(format!(
            "voice-agent-nats-test-{}-{}",
            std::process::id(),
            port
        ));

        let child = std::process::Command::new("nats-server")
            .args(["-p", &port.to_string(), "-c", conf, "-sd"])
            .arg(&store_dir)
            .env("NATS_PROVISIONER_PASSWORD", "changeme_nats_provisioner")
            .env("NATS_SIGNALING_PASSWORD", "changeme_signaling")
            .env("NATS_BRAIN_PASSWORD", "changeme_brain_agent")
            .env("NATS_SUBCONSCIOUS_PASSWORD", "changeme_subconscious_agent")
            .env("NATS_SURFACING_PASSWORD", "changeme_surfacing_agent")
            .env("NATS_SYSTEM_PASSWORD", "changeme_system_agent")
            .env("NATS_TRANSPORT_PASSWORD", "changeme_transport_agent")
            .env("NATS_VISION_PASSWORD", "changeme_vision_agent")
            .env("NATS_STT_PASSWORD", "changeme_stt_agent")
            .env("NATS_VOICE_PASSWORD", "changeme_voice_agent")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("spawn nats-server");
        let guard = NatsServerGuard(child);

        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        loop {
            if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
                break;
            }
            if std::time::Instant::now() > deadline {
                panic!("nats-server did not open its port in time");
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
        Some((guard, port))
    }

    /// P2-1: `connect_nats` must actually authenticate against the real
    /// shipped accounts file when `NATS_USER`/`NATS_PASSWORD` are set --
    /// the Rust half of the same opt-in mechanism
    /// `test_nats_accounts_enforcement.py` proves for the Python half.
    #[tokio::test]
    async fn connect_nats_authenticates_with_correct_credentials() {
        let Some((_guard, port)) = spawn_accounts_server().await else {
            return;
        };

        let result = connect_nats(
            &format!("nats://127.0.0.1:{port}"),
            Some("voice_agent".to_string()),
            Some("changeme_voice_agent".to_string()),
        )
        .await;

        assert!(
            result.is_ok(),
            "connect_nats with the real voice_agent credentials must succeed: {:?}",
            result.err()
        );
    }

    #[tokio::test]
    async fn connect_nats_rejects_wrong_credentials() {
        let Some((_guard, port)) = spawn_accounts_server().await else {
            return;
        };

        let result = connect_nats(
            &format!("nats://127.0.0.1:{port}"),
            Some("voice_agent".to_string()),
            Some("not-the-real-password".to_string()),
        )
        .await;

        assert!(
            result.is_err(),
            "a wrong password must not be allowed to connect"
        );
    }

    #[tokio::test]
    async fn connect_nats_connects_anonymously_when_no_credentials_are_given() {
        // No accounts server here -- an ordinary, unauthenticated local
        // nats-server (or none at all) is the default-deployment case this
        // opt-in feature must leave completely unchanged.
        let url = std::env::var("NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".to_string());
        if async_nats::connect(&url).await.is_err() {
            eprintln!("SKIP: no plain NATS at {url}");
            return;
        }

        let result = connect_nats(&url, None, None).await;
        assert!(
            result.is_ok(),
            "no credentials given must still connect normally"
        );
    }
}
