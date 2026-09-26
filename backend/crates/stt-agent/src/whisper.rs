//! Real speech recognition backed by whisper.cpp (via `whisper-rs`).
//!
//! Dual-path design: a small `fast` model produces low-latency speculative
//! hypotheses for barge-in arbitration, while a larger `accurate` model produces
//! the final transcript that drives cognition. Both are Whisper — the historical
//! docs described the fast path as SenseVoice, but that is a sherpa-onnx model and
//! is not available through whisper.cpp. Emotion / paralinguistic events are
//! therefore NOT inferred here; those fields are left empty rather than fabricated.

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tracing::{info, warn};
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

/// Upstream ggml weights published alongside whisper.cpp.
const MODEL_BASE_URL: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main";

/// Bucket 1 (VOICE_REMEDIATION_PLAN.md): GGML-converted Silero VAD weights, hosted in a
/// separate repo from the whisper.cpp models above. Confirmed by fetching whisper.cpp's
/// own `models/download-vad-model.sh` directly rather than guessing a URL -- an earlier
/// attempt at `{MODEL_BASE_URL}/ggml-silero-v5.1.2.bin` 404'd, since it lives here instead.
const VAD_MODEL_BASE_URL: &str = "https://huggingface.co/ggml-org/whisper-vad/resolve/main";

/// P2-12: pinned SHA256 checksums for the ggml weights this repo actually
/// ships defaults for, mirroring `provision_models.py`'s existing SenseVoice
/// pin -- computed directly from a fresh download of each file, not copied
/// from an unverified source (`shasum -a 256 ggml-<name>.bin`).
///
/// `STT_FAST_MODEL`/`STT_ACCURATE_MODEL` are operator-configurable to any
/// whisper.cpp release name, not just these two, so this is deliberately
/// not exhaustive: an unlisted model name logs a warning and proceeds
/// unverified (`expected_sha256` returns `None`) rather than refusing to
/// start, since a real absent entry and a would-be-wrong hardcoded one are
/// indistinguishable to a hard gate but very different in consequence -- a
/// wrong pin permanently blocks a legitimate model, an absent one just
/// means "not verified," honestly labeled as such in the log.
const PINNED_MODEL_SHA256: &[(&str, &str)] = &[
    (
        "tiny.en",
        "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f",
    ),
    (
        "base.en",
        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
    ),
];

fn expected_sha256(model_name: &str) -> Option<&'static str> {
    PINNED_MODEL_SHA256
        .iter()
        .find(|(name, _)| *name == model_name)
        .map(|(_, sha)| *sha)
}

/// Same reasoning as `PINNED_MODEL_SHA256` above, computed the same way: downloaded for
/// real from `VAD_MODEL_BASE_URL` on home-gpu (2026-09-01) and hashed with `sha256sum`,
/// not copied from an unverified source.
const PINNED_VAD_MODEL_SHA256: &[(&str, &str)] = &[(
    "silero-v5.1.2",
    "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf",
)];

fn expected_vad_sha256(model_name: &str) -> Option<&'static str> {
    PINNED_VAD_MODEL_SHA256
        .iter()
        .find(|(name, _)| *name == model_name)
        .map(|(_, sha)| *sha)
}

/// Streamed in fixed-size chunks rather than `tokio::fs::read` (whole file
/// into memory at once) -- the same reasoning the download loop below
/// already documents for itself: ggml weights run to hundreds of MB, and
/// this runs on every cache hit, not just fresh downloads.
async fn sha256_hex(path: &Path) -> Result<String> {
    let mut file = tokio::fs::File::open(path)
        .await
        .with_context(|| format!("open {} for checksum", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 1 << 16];
    loop {
        let n = file
            .read(&mut buf)
            .await
            .with_context(|| format!("read {} for checksum", path.display()))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

pub struct WhisperModel {
    ctx: WhisperContext,
    label: &'static str,
    language: String,
    /// Bucket 1 (VOICE_REMEDIATION_PLAN.md): `None` means whisper.cpp's own internal VAD
    /// gating stays off (today's behavior, unchanged). Set via `with_vad_model` -- a
    /// builder method rather than a `load()` parameter, so the three existing call sites
    /// that don't need VAD are untouched. This complements, not replaces, `Endpointer` in
    /// `audio.rs`: `Endpointer` still makes the real-time decision of when an utterance
    /// starts/ends from the live stream; this operates on the already-buffered utterance
    /// `Endpointer` hands over, further filtering which sub-portions of it are speech
    /// before the expensive decode step runs -- exactly whisper.cpp's own intended use of
    /// this feature, not a real-time streaming VAD.
    vad_model_path: Option<PathBuf>,
}

impl WhisperModel {
    pub fn load(path: &Path, label: &'static str, language: &str) -> Result<Self> {
        // `new_with_params` is generic over `AsRef<Path>`, so the path goes in directly.
        let ctx = WhisperContext::new_with_params(path, WhisperContextParameters::default())
            .with_context(|| format!("load whisper model '{label}' from {}", path.display()))?;

        info!(model = label, path = %path.display(), "loaded whisper model");
        Ok(Self {
            ctx,
            label,
            language: language.to_string(),
            vad_model_path: None,
        })
    }

    /// Enable whisper.cpp's internal VAD gating using a GGML-converted Silero model
    /// (see `ensure_vad_model`). Chainable so the three existing `load()` call sites that
    /// don't pass a VAD model are unaffected.
    pub fn with_vad_model(mut self, path: Option<PathBuf>) -> Self {
        self.vad_model_path = path;
        self
    }

    /// Transcribe mono 16 kHz f32 PCM. Returns the concatenated segment text.
    ///
    /// This is synchronous, CPU-bound work — callers must run it off the async
    /// runtime (e.g. `tokio::task::spawn_blocking`).
    pub fn transcribe(&self, pcm_16k: &[f32]) -> Result<String> {
        // whisper.cpp needs at least ~1 second of audio to produce a usable mel
        // window; shorter buffers yield garbage or an internal error.
        if pcm_16k.len() < 16_000 / 2 {
            return Ok(String::new());
        }

        let mut state = self
            .ctx
            .create_state()
            .with_context(|| format!("create whisper state for '{}'", self.label))?;

        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_language(Some(self.language.as_str()));
        params.set_translate(false);
        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        params.set_suppress_blank(true);
        // Decode-time suppression of non-speech tokens (e.g. the [MUSIC]/[BLANK_AUDIO]
        // token IDs whisper.cpp's vocabulary carries), complementing rather than
        // duplicating clean_transcript's after-the-fact bracket stripping below, which
        // only catches tokens that already made it into the decoded text.
        params.set_suppress_nst(true);
        // Bucket 1 (VOICE_REMEDIATION_PLAN.md): NOT set_no_speech_thold/set_logprob_thold/
        // set_entropy_thold here, despite that being the audit's literal suggestion --
        // checked whisper-rs 0.16's source first. `set_no_speech_thold`'s own doc comment
        // says "Currently (as of v1.3.0) not implemented" in the whisper.cpp version this
        // crate binds, and `FullParams::new` already seeds logprob_thold/entropy_thold from
        // whisper.cpp's own `whisper_full_default_params()`, so setting them to the same
        // defaults here would be a no-op that looks like a fix without being one. The value
        // whisper.cpp DOES compute and expose for real is the per-segment probability below.
        // Single-segment decoding keeps latency predictable for short utterances.
        params.set_n_threads(recommended_threads());

        // Bucket 1: further gate on Silero VAD probability within the already-buffered
        // utterance when a model is configured -- `Endpointer` (audio.rs) is still what
        // decided this buffer's boundaries in real time.
        if let Some(vad_path) = self.vad_model_path.as_ref().and_then(|p| p.to_str()) {
            params.set_vad_model_path(Some(vad_path));
            params.set_vad_params(whisper_rs::WhisperVadParams::new());
            params.enable_vad(true);
        }

        state
            .full(params, pcm_16k)
            .with_context(|| format!("whisper inference failed for '{}'", self.label))?;

        // whisper-rs 0.16 exposes segments through an iterator; `to_str_lossy`
        // replaces invalid UTF-8 rather than failing the whole utterance, which
        // matters because whisper can emit partial multi-byte tokens.
        let mut text = String::new();
        for segment in state.as_iter() {
            let no_speech_prob = segment.no_speech_probability();
            if no_speech_prob > NO_SPEECH_PROBABILITY_THRESHOLD {
                warn!(
                    model = self.label,
                    no_speech_prob,
                    "dropping whisper segment likely hallucinated from non-speech audio"
                );
                continue;
            }
            match segment.to_str_lossy() {
                Ok(s) => text.push_str(&s),
                Err(err) => warn!(
                    model = self.label,
                    "skipping undecodable whisper segment: {err}"
                ),
            }
        }

        let cleaned = clean_transcript(&text);
        if is_known_hallucination(&cleaned) {
            warn!(
                model = self.label,
                transcript = %cleaned,
                "dropping transcript matching a known Whisper noise-hallucination phrase"
            );
            return Ok(String::new());
        }

        Ok(cleaned)
    }
}

/// whisper.cpp's own standard default for `no_speech_thold` (unused as a decode-time
/// gate in this whisper.cpp version -- see the comment above -- but still meaningful
/// as the threshold for the value it computes and exposes per segment).
const NO_SPEECH_PROBABILITY_THRESHOLD: f32 = 0.6;

/// Stock short phrases whisper.cpp is well known to hallucinate from silence or room
/// noise, rather than any of the countless things a real short utterance could be.
/// Sourced from this project's own ledger (`"And no..."`, `"Bye!"` were both recorded
/// as observed hallucinations from a real session) plus the phrases most commonly
/// reported across the whisper.cpp community for the same failure mode. Matched
/// case-insensitively against the whole cleaned transcript, not as a substring --
/// a real utterance that happens to end in "thank you" must not be discarded.
const KNOWN_HALLUCINATION_PHRASES: &[&str] = &[
    "and no",
    "bye",
    "bye!",
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching!",
];

fn is_known_hallucination(transcript: &str) -> bool {
    let normalized = transcript.trim().trim_end_matches('.').to_lowercase();
    !normalized.is_empty()
        && KNOWN_HALLUCINATION_PHRASES
            .iter()
            .any(|phrase| phrase.trim_end_matches('.') == normalized)
}

fn recommended_threads() -> std::os::raw::c_int {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4);
    // Cap so a transcription cannot starve the rest of the mesh on small hosts.
    cores.clamp(1, 8) as std::os::raw::c_int
}

/// Strip whisper.cpp's non-speech annotations and normalise whitespace.
///
/// whisper emits bracketed markers such as `[BLANK_AUDIO]`, `(wind blowing)` and
/// `[Music]` for non-speech. Publishing those onto `chat.input` would make the
/// agent "hear" sound effects as user utterances.
pub fn clean_transcript(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    let mut depth_square = 0usize;
    let mut depth_round = 0usize;

    for ch in raw.chars() {
        match ch {
            '[' => depth_square += 1,
            ']' => depth_square = depth_square.saturating_sub(1),
            '(' => depth_round += 1,
            ')' => depth_round = depth_round.saturating_sub(1),
            _ if depth_square == 0 && depth_round == 0 => out.push(ch),
            _ => {}
        }
    }

    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Whisper's smallest real weights (tiny.en) run well over 100 MB, so anything under
/// this is unambiguously a failed/partial download, never a genuine model.
const MIN_VALID_WHISPER_MODEL_BYTES: u64 = 1_000_000;

/// Bucket 1 (VOICE_REMEDIATION_PLAN.md): the Silero VAD weights are legitimately only
/// ~885 KB -- `MIN_VALID_WHISPER_MODEL_BYTES` was tuned for whisper's own much larger
/// weights and flagged a correctly-downloaded, SHA256-matching VAD file as "truncated"
/// on every single run, forcing a full re-download from HuggingFace every process start
/// instead of ever caching. Caught by an actual live run (`--transcribe-file`), not by
/// the unit tests -- this file's size threshold has no test coverage.
const MIN_VALID_VAD_MODEL_BYTES: u64 = 100_000;

/// Resolve a model file, downloading it into the cache directory on first use.
pub async fn ensure_model(cache_dir: &Path, model_name: &str) -> Result<PathBuf> {
    ensure_ggml_file(
        cache_dir,
        model_name,
        MODEL_BASE_URL,
        expected_sha256(model_name),
        MIN_VALID_WHISPER_MODEL_BYTES,
    )
    .await
}

/// Bucket 1 (VOICE_REMEDIATION_PLAN.md): same provisioning contract as `ensure_model`
/// (cache, verify, re-download on mismatch, pin the SHA256), pointed at the separate
/// Silero VAD repo and pin table instead. `model_name` is e.g. `"silero-v5.1.2"`.
pub async fn ensure_vad_model(cache_dir: &Path, model_name: &str) -> Result<PathBuf> {
    ensure_ggml_file(
        cache_dir,
        model_name,
        VAD_MODEL_BASE_URL,
        expected_vad_sha256(model_name),
        MIN_VALID_VAD_MODEL_BYTES,
    )
    .await
}

async fn ensure_ggml_file(
    cache_dir: &Path,
    model_name: &str,
    base_url: &str,
    pin: Option<&str>,
    min_valid_size: u64,
) -> Result<PathBuf> {
    tokio::fs::create_dir_all(cache_dir)
        .await
        .with_context(|| format!("create model cache dir {}", cache_dir.display()))?;

    let file_name = format!("ggml-{model_name}.bin");
    let target = cache_dir.join(&file_name);

    if tokio::fs::try_exists(&target).await.unwrap_or(false) {
        let size = tokio::fs::metadata(&target)
            .await
            .map(|m| m.len())
            .unwrap_or(0);
        if size > min_valid_size {
            match pin {
                None => {
                    info!(model = model_name, path = %target.display(), size, "using cached whisper model (no pinned checksum for this model name)");
                    return Ok(target);
                }
                Some(expected) => match sha256_hex(&target).await {
                    Ok(actual) if actual.eq_ignore_ascii_case(expected) => {
                        info!(model = model_name, path = %target.display(), size, "using cached whisper model (SHA256 verified)");
                        return Ok(target);
                    }
                    Ok(actual) => {
                        warn!(
                            model = model_name,
                            path = %target.display(),
                            expected,
                            actual,
                            "cached whisper model failed SHA256 verification; re-downloading"
                        );
                    }
                    Err(e) => {
                        warn!(model = model_name, path = %target.display(), "failed to checksum cached model ({e:#}); re-downloading");
                    }
                },
            }
        } else {
            warn!(
                path = %target.display(),
                size,
                "cached whisper model looks truncated; re-downloading"
            );
        }
        let _ = tokio::fs::remove_file(&target).await;
    }

    let url = format!("{base_url}/{file_name}");
    info!(model = model_name, %url, "downloading whisper model (first run only)");

    let mut response = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(1800))
        .build()?
        .get(&url)
        .send()
        .await
        .with_context(|| format!("download {url}"))?
        .error_for_status()
        .with_context(|| format!("download {url}"))?;

    // Write to a temp path then rename, so a crash mid-download cannot leave a
    // corrupt file that later looks like a valid cache hit.
    //
    // Streamed chunk-by-chunk rather than via `response.bytes()`, which would hold
    // the entire model in memory before the first byte reaches disk — a spike as
    // large as the model itself (hundreds of MB for the larger ggml weights) at
    // startup, inside a memory-capped container, purely to write a file.
    let tmp = target.with_extension("part");
    let mut file = tokio::fs::File::create(&tmp)
        .await
        .with_context(|| format!("create {}", tmp.display()))?;

    let mut written: u64 = 0;
    while let Some(chunk) = response.chunk().await.context("read model body")? {
        file.write_all(&chunk)
            .await
            .with_context(|| format!("write {}", tmp.display()))?;
        written += chunk.len() as u64;
    }
    file.flush()
        .await
        .with_context(|| format!("flush {}", tmp.display()))?;
    drop(file);

    tokio::fs::rename(&tmp, &target)
        .await
        .with_context(|| format!("finalise {}", target.display()))?;

    // P2-12: verify before trusting a fresh download, the same way
    // provision_models.py refuses to report SenseVoice provisioned until
    // both artifacts hash correctly -- a truncated or tampered download
    // must not become "the cached model" for every run after this one.
    match pin {
        None => warn!(
            model = model_name,
            "no pinned SHA256 for this model name; downloaded but unverified"
        ),
        Some(expected) => {
            let actual = sha256_hex(&target).await?;
            if !actual.eq_ignore_ascii_case(expected) {
                let _ = tokio::fs::remove_file(&target).await;
                anyhow::bail!(
                    "whisper model {model_name} failed SHA256 verification after download: \
                     expected {expected}, got {actual}. Refusing to use it."
                );
            }
            info!(
                model = model_name,
                "downloaded whisper model verified (SHA256 match)"
            );
        }
    }

    info!(model = model_name, bytes = written, "whisper model ready");
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expected_sha256_returns_the_pin_for_a_known_model() {
        assert_eq!(
            expected_sha256("tiny.en"),
            Some("921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f")
        );
        assert_eq!(
            expected_sha256("base.en"),
            Some("a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002")
        );
    }

    #[test]
    fn expected_sha256_is_none_for_an_unpinned_model() {
        // STT_FAST_MODEL/STT_ACCURATE_MODEL are operator-configurable to any
        // whisper.cpp release name -- an unlisted one must not panic or
        // silently match, it must come back None so the caller can log a
        // named "unverified" warning instead of a false pass.
        assert_eq!(expected_sha256("large-v3"), None);
        assert_eq!(expected_sha256("not-a-real-model"), None);
    }

    #[test]
    fn expected_vad_sha256_returns_the_pin_for_the_known_model() {
        // Downloaded for real from VAD_MODEL_BASE_URL on home-gpu (2026-09-01) and
        // hashed with sha256sum, not copied from an unverified source -- same
        // integrity bar as the whisper model pins above.
        assert_eq!(
            expected_vad_sha256("silero-v5.1.2"),
            Some("29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf")
        );
    }

    #[test]
    fn vad_model_size_threshold_accepts_the_real_downloaded_file_size() {
        // The real bug, caught by an actual `--transcribe-file` run, not by any unit
        // test: MIN_VALID_WHISPER_MODEL_BYTES (1_000_000) was reused for the VAD file
        // too, and the real ggml-silero-v5.1.2.bin is only 885_098 bytes -- so a
        // correctly-downloaded, SHA256-verified VAD file was flagged "truncated" and
        // re-downloaded from HuggingFace on every single process start.
        const REAL_SILERO_V5_1_2_SIZE_BYTES: u64 = 885_098;
        const {
            assert!(REAL_SILERO_V5_1_2_SIZE_BYTES > MIN_VALID_VAD_MODEL_BYTES);
        }
        // And the whisper threshold must stay far above the VAD one, or this test
        // would pass for the wrong reason (both thresholds collapsing to ~0).
        const {
            assert!(MIN_VALID_WHISPER_MODEL_BYTES > REAL_SILERO_V5_1_2_SIZE_BYTES);
        }
    }

    #[test]
    fn expected_vad_sha256_is_none_for_an_unpinned_model() {
        // STT_VAD_MODEL is operator-configurable (e.g. to "silero-v6.2.0", which
        // whisper.cpp's own download script also lists) -- an unlisted one must
        // come back None, not panic or silently match a different model's pin.
        assert_eq!(expected_vad_sha256("silero-v6.2.0"), None);
        assert_eq!(expected_vad_sha256("not-a-real-model"), None);
    }

    #[tokio::test]
    async fn sha256_hex_matches_a_known_digest_of_small_content() {
        let dir = std::env::temp_dir().join(format!("whisper-sha-test-{}", std::process::id()));
        tokio::fs::create_dir_all(&dir).await.unwrap();
        let path = dir.join("sample.bin");
        tokio::fs::write(&path, b"hello world").await.unwrap();

        let digest = sha256_hex(&path).await.unwrap();

        // echo -n "hello world" | shasum -a 256
        assert_eq!(
            digest,
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        );

        let _ = tokio::fs::remove_dir_all(&dir).await;
    }

    #[tokio::test]
    async fn sha256_hex_is_sensitive_to_a_single_changed_byte() {
        let dir = std::env::temp_dir().join(format!("whisper-sha-test2-{}", std::process::id()));
        tokio::fs::create_dir_all(&dir).await.unwrap();
        let a = dir.join("a.bin");
        let b = dir.join("b.bin");
        tokio::fs::write(&a, b"identical content except one byte-A")
            .await
            .unwrap();
        tokio::fs::write(&b, b"identical content except one byte-B")
            .await
            .unwrap();

        let digest_a = sha256_hex(&a).await.unwrap();
        let digest_b = sha256_hex(&b).await.unwrap();

        assert_ne!(
            digest_a, digest_b,
            "a tampered/corrupted download must not hash the same as the real file"
        );

        let _ = tokio::fs::remove_dir_all(&dir).await;
    }

    #[test]
    fn clean_strips_bracketed_non_speech() {
        assert_eq!(clean_transcript(" [BLANK_AUDIO] "), "");
        assert_eq!(clean_transcript("[Music] hello there"), "hello there");
        assert_eq!(
            clean_transcript("hello (wind blowing) world"),
            "hello world"
        );
    }

    #[test]
    fn clean_normalises_whitespace() {
        assert_eq!(
            clean_transcript("  hello   there \n world "),
            "hello there world"
        );
    }

    #[test]
    fn clean_keeps_plain_speech_intact() {
        assert_eq!(
            clean_transcript("Turn the lights off."),
            "Turn the lights off."
        );
    }

    #[test]
    fn known_hallucination_phrases_are_rejected_case_and_punctuation_insensitively() {
        assert!(is_known_hallucination("Bye!"));
        assert!(is_known_hallucination("bye"));
        assert!(is_known_hallucination("Thank you."));
        assert!(is_known_hallucination("THANK YOU"));
        assert!(is_known_hallucination("And no..."));
    }

    #[test]
    fn a_real_utterance_ending_in_a_hallucination_phrase_is_not_rejected() {
        // The denylist matches the whole transcript, not a substring -- a real
        // utterance that happens to end with "thank you" must survive.
        assert!(!is_known_hallucination(
            "I really appreciate the help, thank you"
        ));
        assert!(!is_known_hallucination("Could you say bye to her for me"));
    }

    #[test]
    fn a_bare_you_is_a_legitimate_reply_not_a_hallucination() {
        // "you" alone is a normal short answer ("who's there?" / "you") --
        // three independent reviewers flagged this exact entry when it was in
        // the denylist, since it silently dropped real user turns.
        assert!(!is_known_hallucination("you"));
        assert!(!is_known_hallucination("  You  "));
    }

    #[test]
    fn empty_transcript_is_not_flagged_as_a_hallucination() {
        // Empty is already "no speech" via the caller's own length guard; this
        // function's job is distinguishing confident-but-wrong speech from
        // genuine short utterances, not re-deciding silence.
        assert!(!is_known_hallucination(""));
        assert!(!is_known_hallucination("   "));
    }
}
