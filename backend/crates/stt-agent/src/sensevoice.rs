//! Acoustic perception via SenseVoice (sherpa-onnx).
//!
//! This restores the half of HNNA's affect pillar that the Rust migration dropped.
//! SenseVoice transcribes *and* classifies speech emotion and audio events; Whisper
//! only transcribes. The consumer for that data has been live in Python the whole
//! time (`StateService.apply_sensory_perception`), blending acoustic emotion into
//! mood and `user_mental_model.inferred_valence`, and reacting to laughter,
//! applause and coughing. Since the migration it has received nothing on every
//! perception, so the agent has been deaf to *how* the user sounds.
//!
//! SenseVoice encodes its classifications as inline tags in the transcript, e.g.
//! `<|en|><|HAPPY|><|Speech|><|withitn|>that's wonderful`. This module strips them
//! back out and maps emotion onto the scalar bias the Python state machine expects.
//! The tag vocabulary and the emotion->bias mapping are ported verbatim from the
//! pre-migration `sensevoice_service.py` so the values the state machine sees are
//! unchanged; see `emotion_bias` for why they are not re-derived here.

use anyhow::Result;
use sherpa_onnx::{OfflineRecognizer, OfflineRecognizerConfig, OfflineSenseVoiceModelConfig};
use std::path::Path;
use tracing::{info, warn};

/// Emotions SenseVoice can emit, and their valence bias.
///
/// Ported verbatim from the archived `sensevoice_service.py` `emotion_map`. These
/// feed a damped bias state machine on the Python side that was tuned against these
/// exact numbers; changing them here would silently retune the agent's affect
/// response, so they are kept identical rather than "improved".
const EMOTION_BIAS: [(&str, f64); 7] = [
    ("HAPPY", 0.4),
    ("SAD", -0.35),
    ("ANGRY", -0.6),
    ("NEUTRAL", 0.0),
    ("FEARFUL", -0.4),
    ("DISGUSTED", -0.5),
    ("SURPRISED", 0.2),
];

/// Audio events SenseVoice can emit. Only these are forwarded; unknown tags (the
/// language and ITN markers, mostly) are stripped without being reported as events.
const KNOWN_EVENTS: [&str; 8] = [
    "BGM", "Speech", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough",
];

/// What SenseVoice heard: the words, and how they sounded.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Perception {
    /// Transcript with all `<|tag|>` markers removed.
    pub text: String,
    /// Strongest emotion label, if the model emitted one.
    pub emotion: Option<String>,
    /// Valence bias for `emotion`, or `None` when no emotion was classified.
    ///
    /// `None` and `Some(0.0)` are NOT interchangeable: `Some(0.0)` is a real neutral
    /// reading, while `None` means the model offered no estimate. The Python state
    /// machine distinguishes them — blending an absent estimate as 0.0 flattens mood
    /// toward zero on every perception — so the distinction must survive this far.
    pub emotional_bias: Option<f64>,
    /// Recognised audio events (`Laughter`, `Cough`, ...), in order of appearance.
    pub events: Vec<String>,
}

pub struct SenseVoiceModel {
    recognizer: OfflineRecognizer,
}

impl SenseVoiceModel {
    /// Load SenseVoice from a sherpa-onnx model directory.
    ///
    /// Expects `model.onnx` (or `model.int8.onnx`) plus `tokens.txt`, the layout of
    /// the upstream `sherpa-onnx-sense-voice-*` release that
    /// `scripts/bootstrap/provision_models.py` already downloads and SHA-pins.
    pub fn load(model_dir: &Path, language: &str) -> Result<Self> {
        let model = resolve_model_file(model_dir)?;
        let tokens = model_dir.join("tokens.txt");
        if !tokens.exists() {
            anyhow::bail!(
                "SenseVoice tokens.txt not found at {}. The sherpa-onnx SenseVoice \
                 release ships model.int8.onnx + tokens.txt together; provision it \
                 with scripts/bootstrap/provision_models.py.",
                tokens.display()
            );
        }

        let mut config = OfflineRecognizerConfig::default();
        config.model_config.sense_voice = OfflineSenseVoiceModelConfig {
            model: Some(model.to_string_lossy().into_owned()),
            language: Some(language.to_string()),
            // Inverse text normalisation: "twenty five" -> "25". The words reach
            // cognition, so readable numerals are worth having.
            use_itn: true,
        };
        config.model_config.tokens = Some(tokens.to_string_lossy().into_owned());

        // `create` returns Option, not Result: sherpa reports failure by yielding
        // None, with the reason only on its own stderr. Convert to a contextful error
        // so an unloadable model does not become a bare "None" in the logs.
        let recognizer = OfflineRecognizer::create(&config).ok_or_else(|| {
            anyhow::anyhow!(
                "sherpa-onnx rejected the SenseVoice model at {} (tokens: {}); \
                 check the files are a matching sherpa-onnx SenseVoice release",
                model.display(),
                tokens.display()
            )
        })?;

        info!(
            model = %model.display(),
            language,
            "SenseVoice loaded: acoustic emotion and event perception active"
        );
        Ok(Self { recognizer })
    }

    /// Transcribe 16 kHz mono f32 samples and extract acoustic affect.
    pub fn perceive(&self, pcm_16k: &[f32]) -> Result<Perception> {
        let stream = self.recognizer.create_stream();
        stream.accept_waveform(16_000, pcm_16k);
        self.recognizer.decode(&stream);

        let result = stream
            .get_result()
            .ok_or_else(|| anyhow::anyhow!("SenseVoice returned no result"))?;

        Ok(parse_tagged_transcript(&result.text))
    }
}

/// Pick the model file, preferring the quantised build.
///
/// The upstream release ships `model.int8.onnx`; some mirrors ship `model.onnx`.
/// Checking both avoids a provisioning layout change silently disabling perception.
fn resolve_model_file(model_dir: &Path) -> Result<std::path::PathBuf> {
    for name in ["model.int8.onnx", "model.onnx"] {
        let candidate = model_dir.join(name);
        if candidate.exists() {
            return Ok(candidate);
        }
    }
    anyhow::bail!(
        "no SenseVoice model found in {} (looked for model.int8.onnx, model.onnx)",
        model_dir.display()
    )
}

/// Split a SenseVoice transcript into words, emotion and events.
///
/// SenseVoice emits `<|lang|><|EMOTION|><|Event|><|itn|>text`. Tags are extracted
/// and removed; unknown tags (language codes, `withitn`/`woitn`) are dropped without
/// comment. The first recognised emotion wins, matching the pre-migration Python,
/// which took `emotions[0]` as the primary.
pub fn parse_tagged_transcript(raw: &str) -> Perception {
    let mut emotion = None;
    let mut events = Vec::new();
    let mut text = String::with_capacity(raw.len());

    let mut rest = raw;
    while let Some(start) = rest.find("<|") {
        text.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let Some(end) = after.find("|>") else {
            // Unterminated tag: treat the remainder as text rather than dropping it.
            text.push_str(&rest[start..]);
            rest = "";
            break;
        };
        let tag = &after[..end];

        if emotion.is_none() && EMOTION_BIAS.iter().any(|(name, _)| *name == tag) {
            emotion = Some(tag.to_string());
        } else if KNOWN_EVENTS.contains(&tag) {
            events.push(tag.to_string());
        }

        rest = &after[end + 2..];
    }
    text.push_str(rest);

    let emotional_bias = emotion.as_deref().and_then(emotion_bias);

    Perception {
        text: text.split_whitespace().collect::<Vec<_>>().join(" "),
        emotion,
        emotional_bias,
        events,
    }
}

/// Valence bias for a SenseVoice emotion label, or `None` if unrecognised.
///
/// Returning `None` for an unknown label rather than defaulting to 0.0 keeps a
/// model that grows a new emotion class from being reported as "sounds neutral".
pub fn emotion_bias(emotion: &str) -> Option<f64> {
    EMOTION_BIAS
        .iter()
        .find(|(name, _)| *name == emotion)
        .map(|(_, bias)| *bias)
}

/// Warn once if a model directory looks unprovisioned, so the operator learns why
/// perception is degraded instead of silently losing affect.
pub fn warn_if_unavailable(model_dir: &Path, err: &anyhow::Error) {
    warn!(
        dir = %model_dir.display(),
        "SenseVoice unavailable ({err:#}); falling back to Whisper for the fast path. \
         Acoustic emotion and audio events will NOT be perceived: the agent hears the \
         user's words but not their tone."
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_emotion_events_and_text() {
        let p = parse_tagged_transcript("<|en|><|HAPPY|><|Laughter|><|withitn|>that is wonderful");
        assert_eq!(p.text, "that is wonderful");
        assert_eq!(p.emotion.as_deref(), Some("HAPPY"));
        assert_eq!(p.emotional_bias, Some(0.4));
        assert_eq!(p.events, vec!["Laughter"]);
    }

    #[test]
    fn language_and_itn_tags_are_not_events() {
        // The regression this guards: treating every tag as an event would publish
        // "en" and "withitn" onto paralinguistic_events, and the Python state machine
        // would iterate them looking for Laughter/Applause.
        let p = parse_tagged_transcript("<|zh|><|NEUTRAL|><|Speech|><|woitn|>hello");
        assert_eq!(p.events, vec!["Speech"]);
        assert_eq!(p.text, "hello");
    }

    #[test]
    fn anger_maps_to_the_python_bias() {
        // Value ported from sensevoice_service.py; the Python damped-bias state
        // machine is tuned to it.
        let p = parse_tagged_transcript("<|en|><|ANGRY|><|Speech|><|withitn|>fine");
        assert_eq!(p.emotional_bias, Some(-0.6));
    }

    #[test]
    fn neutral_is_a_real_reading_not_an_absence() {
        // Some(0.0) must not collapse into None: an explicit neutral from the model
        // is evidence and is blended, whereas absence must be skipped.
        let p = parse_tagged_transcript("<|en|><|NEUTRAL|><|Speech|><|withitn|>ok");
        assert_eq!(p.emotional_bias, Some(0.0));
        assert!(p.emotional_bias.is_some());
    }

    #[test]
    fn untagged_transcript_yields_no_emotion() {
        let p = parse_tagged_transcript("just plain words");
        assert_eq!(p.text, "just plain words");
        assert_eq!(p.emotion, None);
        assert_eq!(p.emotional_bias, None, "absence must not become 0.0");
        assert!(p.events.is_empty());
    }

    #[test]
    fn multiple_events_are_all_reported() {
        let p = parse_tagged_transcript("<|en|><|HAPPY|><|Laughter|><|Applause|><|withitn|>yes");
        assert_eq!(p.events, vec!["Laughter", "Applause"]);
    }

    #[test]
    fn first_emotion_wins_like_the_python_primary() {
        let p = parse_tagged_transcript("<|SAD|><|HAPPY|>mixed");
        assert_eq!(p.emotion.as_deref(), Some("SAD"));
    }

    #[test]
    fn unterminated_tag_does_not_swallow_the_transcript() {
        let p = parse_tagged_transcript("<|en|>hello <|broken");
        assert!(p.text.contains("hello"), "got {:?}", p.text);
    }

    #[test]
    fn unknown_emotion_label_has_no_bias() {
        assert_eq!(emotion_bias("EUPHORIC"), None);
        assert_eq!(emotion_bias("ANGRY"), Some(-0.6));
    }

    /// Runtime proof the sherpa-onnx API usage is right, not just type-correct:
    /// a wrong config field surfaces here as `create()` returning `None`, which
    /// no amount of compiling can catch. Skips (loudly) when the model is not
    /// provisioned, mirroring voice-agent's real-model tests.
    #[test]
    fn real_model_loads_and_perceives_audio() {
        let model_dir =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../models/sensevoice");
        if !model_dir.join("tokens.txt").exists() {
            eprintln!("SKIP: models/sensevoice not provisioned");
            return;
        }

        let model = SenseVoiceModel::load(&model_dir, "en")
            .expect("provisioned SenseVoice model must load");

        // One second of a 220 Hz tone: not speech, so we assert only the runtime
        // contract — inference completes and yields a Perception — never what the
        // model "heard" in a synthetic hum.
        let pcm: Vec<f32> = (0..16_000)
            .map(|i| 0.1 * (i as f32 * 220.0 * 2.0 * std::f32::consts::PI / 16_000.0).sin())
            .collect();
        let perception = model.perceive(&pcm).expect("inference must complete");
        eprintln!("SenseVoice heard: {perception:?}");
    }
}
