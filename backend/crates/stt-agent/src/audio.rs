//! Audio front-end for the STT agent: rate normalisation and speech endpointing.
//!
//! Whisper requires mono f32 PCM at exactly 16 kHz. Inbound mesh audio arrives at
//! whatever rate the transport negotiated (LiveKit/WebRTC is commonly 48 kHz;
//! `Config.SAMPLE_RATE` is 32 kHz), so it must be resampled rather than
//! reinterpreted. The previous implementation named its buffer `pcm_16k_mono` but
//! never resampled at all, and discarded `STT_TARGET_SAMPLE_RATE` outright.

use std::collections::hash_map::Entry;
use std::collections::HashMap;

use anyhow::{Context, Result};
use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};

pub const WHISPER_SAMPLE_RATE: u32 = 16_000;

/// Decode interleaved little-endian i16 PCM and downmix to mono f32 in [-1.0, 1.0].
pub fn decode_mono_f32(bytes: &[u8], channels: usize) -> Vec<f32> {
    let channels = channels.max(1);
    let samples: Vec<i16> = bytes
        .chunks_exact(2)
        .map(|chunk| i16::from_le_bytes([chunk[0], chunk[1]]))
        .collect();

    let scale = 1.0 / i16::MAX as f32;
    if channels == 1 {
        return samples.iter().map(|&s| s as f32 * scale).collect();
    }

    samples
        .chunks_exact(channels)
        .map(|frame| {
            let total: i32 = frame.iter().map(|s| *s as i32).sum();
            (total as f32 / channels as f32) * scale
        })
        .collect()
}

fn sinc_params() -> SincInterpolationParameters {
    SincInterpolationParameters {
        sinc_len: 128,
        f_cutoff: 0.95,
        interpolation: SincInterpolationType::Linear,
        oversampling_factor: 128,
        window: WindowFunction::BlackmanHarris2,
    }
}

fn new_sinc_resampler(source_rate: u32, chunk_size: usize) -> Result<SincFixedIn<f32>> {
    let ratio = WHISPER_SAMPLE_RATE as f64 / source_rate as f64;
    SincFixedIn::<f32>::new(ratio, 2.0, sinc_params(), chunk_size.max(1), 1)
        .context("construct sinc resampler")
}

/// One-shot resample: builds a resampler, uses it once, discards it.
///
/// This is what every call used to do (M3-P5) -- kept as the fallback for
/// input longer than a `ResamplerCache`'s cached bound, so a pathological
/// input still resamples correctly rather than erroring.
fn resample_one_shot(input: &[f32], source_rate: u32) -> Result<Vec<f32>> {
    let mut resampler = new_sinc_resampler(source_rate, input.len())?;
    let output = resampler
        .process(&[input.to_vec()], None)
        .context("resample to 16 kHz")?;
    Ok(output.into_iter().next().unwrap_or_default())
}

/// Caches a `SincFixedIn` resampler per source sample rate.
///
/// M3-P5: every call to resample a chunk used to construct a brand-new
/// `SincFixedIn`, which builds a sinc/window interpolation table
/// (`oversampling_factor` 128 x `sinc_len` 128) from scratch -- on the STT
/// hot path, called for every speech-confirmed partial (up to one per
/// `partial_interval_ms`) and every endpointed utterance.
///
/// Reuse is safe because of how rubato splits its own state: `reset()`
/// clears only the internal delay-line buffer and restores `chunk_size` to
/// the value passed at construction (`asyncro_sinc.rs::reset`); it does not
/// touch the interpolator table, which `new()` builds once and never
/// rebuilds. Calling `reset()` before every use, then `set_chunk_size()` for
/// the call's actual length, therefore produces output identical to
/// constructing fresh each time -- only the (expensive) interpolator build
/// is skipped, not any state that would make reuse observable.
pub struct ResamplerCache {
    max_utterance_secs: f64,
    resamplers: HashMap<u32, SincFixedIn<f32>>,
}

impl ResamplerCache {
    /// `max_utterance_secs` sizes each rate's cached resampler generously
    /// enough (with a 5% margin) to cover the longest single call it will
    /// ever see for that rate, since a caller bounding utterance length to
    /// this same value (as `handle_audio_inbound`'s force-cut does) can
    /// never hand this cache a longer chunk than that bound implies.
    pub fn new(max_utterance_secs: f64) -> Self {
        Self {
            max_utterance_secs: max_utterance_secs.max(0.001),
            resamplers: HashMap::new(),
        }
    }

    fn max_chunk_size_for(&self, source_rate: u32) -> usize {
        ((source_rate as f64 * self.max_utterance_secs * 1.05).ceil() as usize).max(1)
    }

    /// Resample mono f32 audio to 16 kHz for Whisper.
    ///
    /// Uses a windowed-sinc resampler rather than naive linear interpolation:
    /// the common case here is *downsampling* (48k/32k -> 16k), where
    /// dropping samples without band-limiting folds high-frequency energy
    /// back into the speech band as aliasing and measurably degrades
    /// recognition.
    pub fn resample_to_16k(&mut self, input: &[f32], source_rate: u32) -> Result<Vec<f32>> {
        if input.is_empty() {
            return Ok(Vec::new());
        }
        if source_rate == WHISPER_SAMPLE_RATE {
            return Ok(input.to_vec());
        }
        if source_rate == 0 {
            anyhow::bail!("source sample rate is zero");
        }

        let max_chunk_size = self.max_chunk_size_for(source_rate);
        if input.len() > max_chunk_size {
            // Longer than this rate's cached bound -- should not happen
            // given upstream callers bound utterance length to
            // max_utterance_secs, but resample correctly regardless of
            // whether that bound holds, rather than erroring or truncating.
            return resample_one_shot(input, source_rate);
        }

        let resampler = match self.resamplers.entry(source_rate) {
            Entry::Occupied(entry) => entry.into_mut(),
            Entry::Vacant(entry) => entry.insert(new_sinc_resampler(source_rate, max_chunk_size)?),
        };

        resampler.reset();
        resampler
            .set_chunk_size(input.len())
            .context("set resampler chunk size")?;
        let output = resampler
            .process(&[input.to_vec()], None)
            .context("resample to 16 kHz")?;

        Ok(output.into_iter().next().unwrap_or_default())
    }

    #[cfg(test)]
    fn cached_rate_count(&self) -> usize {
        self.resamplers.len()
    }
}

/// Root-mean-square energy of a mono f32 buffer.
pub fn rms(samples: &[f32]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum_sq: f64 = samples.iter().map(|&s| (s as f64) * (s as f64)).sum();
    (sum_sq / samples.len() as f64).sqrt()
}

/// Speech endpointing state machine.
///
/// Whisper is an utterance model, not a streaming one: audio must be segmented
/// into utterances before recognition. This tracks an adaptive noise floor and
/// reports when speech has started and when a trailing silence long enough to be
/// an endpoint has elapsed.
#[derive(Debug)]
pub struct Endpointer {
    noise_floor: f64,
    speech_active: bool,
    silence_run_ms: f64,
    speech_run_ms: f64,
    /// Speech must exceed noise_floor by this factor to count as voiced.
    speech_factor: f64,
    /// Absolute floor so a silent room cannot make noise_floor ~0 and trigger on hiss.
    min_speech_rms: f64,
    /// Trailing silence required to close an utterance.
    endpoint_silence_ms: f64,
    /// Reject blips shorter than this as non-speech.
    min_speech_ms: f64,
}

/// Fraction of the gap to the new (lower) energy that one chunk may close.
///
/// P2-8: the descent used to be unbounded -- a single chunk assigned the floor
/// outright. Asymmetric with the 0.005 rise on purpose, and by a wide margin:
/// a room getting quieter should be tracked promptly, while a room getting
/// louder must not be allowed to drag the floor up mid-utterance and deafen
/// the detector, which is what the rise rate is slow to prevent.
const NOISE_FLOOR_DESCENT: f64 = 0.1;

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum VadEvent {
    /// No speech in progress.
    Silence,
    /// Speech is ongoing; utterance still open.
    SpeechContinues,
    /// A complete utterance just ended and should be transcribed.
    Endpoint,
}

impl Endpointer {
    pub fn new(endpoint_silence_ms: f64, min_speech_ms: f64) -> Self {
        Self {
            noise_floor: 0.01,
            speech_active: false,
            silence_run_ms: 0.0,
            speech_run_ms: 0.0,
            speech_factor: 3.0,
            min_speech_rms: 0.008,
            endpoint_silence_ms,
            min_speech_ms,
        }
    }

    pub fn noise_floor(&self) -> f64 {
        self.noise_floor
    }

    /// Whether the open utterance has accumulated enough voiced audio to be
    /// believed as speech rather than a blip.
    ///
    /// `push` returns `SpeechContinues` from the *first* voiced chunk, long before
    /// `min_speech_ms` is met, because the utterance buffer must start filling at
    /// onset or speech gets clipped. But a blip that never reaches `min_speech_ms`
    /// is ultimately rejected as `Silence`, so anything speculative driven off
    /// `SpeechContinues` alone (partial inference, and the barge-in `audio.stop`
    /// it can emit) would be acting on audio the endpointer itself does not yet
    /// consider speech. Callers gate that work on this.
    pub fn speech_confirmed(&self) -> bool {
        self.speech_active && self.speech_run_ms >= self.min_speech_ms
    }

    /// Re-seed the noise floor from a known-current energy level.
    ///
    /// P2-8's escape hatch. The floor adapts *only* on non-voiced chunks
    /// (deliberately -- see `push`), which means that if it ever ends up below
    /// the room's actual noise level, every chunk reads as voiced, the
    /// adaptation branch stops running, and nothing can lift it back. That is
    /// a latch, not a transient: the detector reports speech forever and
    /// utterances stop endpointing.
    ///
    /// Bounding the descent (in `push`) stops a single anomalous chunk from
    /// causing it, but cannot stop a genuinely long quiet stretch followed by
    /// the noise returning. So there has to be a way out, and the caller
    /// already knows when to use it: a forced cut at `max_utterance_secs` means
    /// the endpointer has claimed one continuous utterance for longer than any
    /// plausible one, which is exactly the symptom. Re-seeding there does not
    /// weaken the "adapt only on non-speech" rule, because a run that long is
    /// by the caller's own definition not speech.
    pub fn reseed_noise_floor(&mut self, chunk_rms: f64) {
        self.noise_floor = chunk_rms.max(0.0);
        self.speech_active = false;
        self.speech_run_ms = 0.0;
        self.silence_run_ms = 0.0;
    }

    /// Feed one chunk's energy and duration; returns the resulting VAD event.
    pub fn push(&mut self, chunk_rms: f64, chunk_ms: f64) -> VadEvent {
        let threshold = (self.noise_floor * self.speech_factor).max(self.min_speech_rms);
        let is_voiced = chunk_rms > threshold;

        // Adapt the noise floor only on non-speech, so sustained talking cannot
        // drag the floor up and deafen the detector mid-utterance.
        if !is_voiced {
            if chunk_rms < self.noise_floor {
                // P2-8: this used to snap straight to `chunk_rms`. One
                // anomalous near-silent chunk -- a dropout, a muted frame --
                // therefore dropped the floor to ~0, after which `threshold`
                // fell to the flat `min_speech_rms` (0.008). In any room whose
                // ambient level exceeds that, every later chunk then read as
                // voiced, this branch stopped running, and the floor could
                // never recover: a permanently-triggered detector, not a deaf
                // one. The comment on `min_speech_rms` claims it prevents
                // exactly this, but it guards the *threshold*, not the floor,
                // so it never could.
                //
                // Descent is bounded instead. Still fast -- at 20ms chunks
                // this covers most of a genuine drop inside half a second --
                // but no single chunk can move the floor more than a tenth of
                // the way down.
                self.noise_floor = self.noise_floor * (1.0 - NOISE_FLOOR_DESCENT)
                    + chunk_rms * NOISE_FLOOR_DESCENT;
            } else {
                self.noise_floor = self.noise_floor * 0.995 + chunk_rms * 0.005;
            }
        }

        if is_voiced {
            self.speech_active = true;
            self.speech_run_ms += chunk_ms;
            self.silence_run_ms = 0.0;
            return VadEvent::SpeechContinues;
        }

        if !self.speech_active {
            return VadEvent::Silence;
        }

        self.silence_run_ms += chunk_ms;
        if self.silence_run_ms < self.endpoint_silence_ms {
            return VadEvent::SpeechContinues;
        }

        // Trailing silence long enough to close the utterance.
        let had_real_speech = self.speech_run_ms >= self.min_speech_ms;
        self.speech_active = false;
        self.speech_run_ms = 0.0;
        self.silence_run_ms = 0.0;

        if had_real_speech {
            VadEvent::Endpoint
        } else {
            VadEvent::Silence
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(len: usize, amp: f32) -> Vec<f32> {
        (0..len).map(|i| amp * (i as f32 * 0.10).sin()).collect()
    }

    #[test]
    fn decode_mono_passthrough() {
        let bytes = [0x00, 0x40, 0x00, 0xC0]; // 16384, -16384
        let out = decode_mono_f32(&bytes, 1);
        assert_eq!(out.len(), 2);
        assert!((out[0] - 0.5).abs() < 0.01);
        assert!((out[1] + 0.5).abs() < 0.01);
    }

    #[test]
    fn decode_downmixes_stereo() {
        // L=16384, R=-16384 -> mono 0
        let bytes = [0x00, 0x40, 0x00, 0xC0];
        let out = decode_mono_f32(&bytes, 2);
        assert_eq!(out.len(), 1);
        assert!(out[0].abs() < 0.01);
    }

    #[test]
    fn resample_48k_to_16k_thirds_the_length() {
        let input = tone(4800, 0.5);
        let mut cache = ResamplerCache::new(30.0);
        let out = cache.resample_to_16k(&input, 48_000).unwrap();
        let expected = 1600.0;
        assert!(
            (out.len() as f64 - expected).abs() / expected < 0.05,
            "expected ~{expected} samples, got {}",
            out.len()
        );
    }

    #[test]
    fn resample_is_identity_at_16k() {
        let input = tone(320, 0.25);
        let mut cache = ResamplerCache::new(30.0);
        let out = cache.resample_to_16k(&input, 16_000).unwrap();
        assert_eq!(out, input);
    }

    /// M3-P5: the whole point of caching. A reused, reset resampler must
    /// produce the same output a fresh one would -- otherwise the
    /// optimization would be trading transcription quality for CPU time
    /// without anyone deciding to make that trade.
    #[test]
    fn reused_resampler_matches_a_fresh_one_shot_resample() {
        let input = tone(4800, 0.5);

        let mut cache = ResamplerCache::new(30.0);
        // Warm the cache with an unrelated call first, so the second call
        // below genuinely exercises reuse (reset + set_chunk_size), not a
        // first-ever construction.
        let _ = cache.resample_to_16k(&tone(1600, 0.3), 48_000).unwrap();
        let cached_out = cache.resample_to_16k(&input, 48_000).unwrap();

        let one_shot_out = resample_one_shot(&input, 48_000).unwrap();

        assert_eq!(
            cached_out, one_shot_out,
            "a reused resampler must be bit-identical to a fresh one-shot resample"
        );
    }

    /// Consecutive calls with different lengths at the same rate exercise
    /// `set_chunk_size` shrinking and growing back -- the partial path
    /// (fixed short window) and the final path (variable, up to a full
    /// utterance) genuinely differ in length at the same source rate.
    #[test]
    fn cache_handles_varying_chunk_lengths_at_the_same_rate() {
        let mut cache = ResamplerCache::new(30.0);

        let short = tone(1600, 0.3); // ~100ms partial window
        let long = tone(4800, 0.5); // a full-length final utterance

        let short_out = cache.resample_to_16k(&short, 48_000).unwrap();
        let long_out = cache.resample_to_16k(&long, 48_000).unwrap();
        let short_out_again = cache.resample_to_16k(&short, 48_000).unwrap();

        assert_eq!(
            short_out, short_out_again,
            "resampling the same input twice, with a differently-sized call \
             in between, must produce the same output both times"
        );
        assert!(long_out.len() > short_out.len());
        assert_eq!(cache.cached_rate_count(), 1, "one rate, one cache entry");
    }

    #[test]
    fn cache_builds_one_entry_per_distinct_source_rate() {
        let mut cache = ResamplerCache::new(30.0);
        cache.resample_to_16k(&tone(4800, 0.5), 48_000).unwrap();
        cache.resample_to_16k(&tone(3200, 0.5), 32_000).unwrap();
        assert_eq!(cache.cached_rate_count(), 2);
    }

    /// Input longer than the cache's bound for that rate must still
    /// resample correctly (via the one-shot fallback), not error or get
    /// silently truncated.
    #[test]
    fn input_longer_than_the_cached_bound_still_resamples_correctly() {
        // A tiny bound (0.01s at 48kHz is 480 samples) so a normal-sized
        // call clearly exceeds it.
        let mut cache = ResamplerCache::new(0.01);
        let input = tone(4800, 0.5);

        let out = cache.resample_to_16k(&input, 48_000).unwrap();
        let expected = 1600.0;
        assert!(
            (out.len() as f64 - expected).abs() / expected < 0.05,
            "expected ~{expected} samples even past the cached bound, got {}",
            out.len()
        );
    }

    /// P2-8: one anomalous near-silent chunk must not latch the detector into
    /// reporting speech forever.
    ///
    /// The floor adapts only on non-voiced chunks. Before this fix the descent
    /// was unbounded -- a single chunk assigned it outright -- so one dropout
    /// dropped it to ~0, `threshold` collapsed to the flat `min_speech_rms`
    /// (0.008), and in any room noisier than that every later chunk read as
    /// voiced. The adaptation branch then never ran again and the floor could
    /// not recover: not a deaf detector, a permanently triggered one, whose
    /// utterances stop endpointing and whose barge-in fires on room noise.
    #[test]
    fn one_silent_chunk_does_not_latch_the_detector_onto_room_noise() {
        let mut ep = Endpointer::new(500.0, 100.0);
        // A room with real ambient noise, well above min_speech_rms (0.008).
        let ambient = 0.02;
        for _ in 0..2000 {
            ep.push(ambient, 20.0);
        }
        assert!(
            !ep.speech_confirmed(),
            "ambient noise alone must not read as speech before the dropout"
        );

        // One anomalous frame: a dropout, a muted mic, a lost packet.
        ep.push(0.0, 20.0);

        // The room has not changed. It must still read as silence.
        for _ in 0..50 {
            assert_eq!(
                ep.push(ambient, 20.0),
                VadEvent::Silence,
                "ambient noise read as speech after a single silent chunk -- \
                 the noise floor latched below the room"
            );
        }
        // And real speech must still be distinguishable from it.
        assert_eq!(ep.push(0.2, 20.0), VadEvent::SpeechContinues);
    }

    /// The descent still has to be fast enough to be useful: a room that
    /// genuinely goes quiet must be tracked, or the detector stays deaf to
    /// speech that is quiet-but-real. Bounding the rate must not become
    /// refusing to descend.
    #[test]
    fn the_floor_still_follows_a_room_that_genuinely_goes_quiet() {
        let mut ep = Endpointer::new(500.0, 100.0);
        // 0.02 is below the initial threshold (0.01 * 3), so it reaches the
        // adaptation branch and the floor genuinely rises to meet it. Picking
        // a level *above* that threshold would instead read as voiced from the
        // first chunk and never adapt at all -- which is its own defect, and
        // has its own test below.
        let noisy = 0.02;
        for _ in 0..2000 {
            ep.push(noisy, 20.0);
        }
        let noisy_floor = ep.noise_floor();
        assert!(
            noisy_floor > 0.015,
            "floor never rose to the room: {noisy_floor}"
        );

        // The room quietens and stays quiet for one second (50 x 20ms).
        let quiet = 0.001;
        for _ in 0..50 {
            ep.push(quiet, 20.0);
        }

        // Bounding the descent must not become refusing to descend: a second
        // of quiet has to land the floor near the new level, or the detector
        // stays deaf to speech that is quiet but real.
        assert!(
            ep.noise_floor() < quiet * 2.0,
            "floor did not follow the room down within a second: {} -> {}",
            noisy_floor,
            ep.noise_floor()
        );
    }

    /// The same latch, reached without any dropout at all -- found while
    /// writing the test above.
    ///
    /// The floor is constructed at 0.01, so the opening threshold is 0.03. In
    /// a room whose ambient level is above that, the *very first* chunk reads
    /// as voiced, the non-speech adaptation branch never runs even once, and
    /// the floor stays pinned at its default forever. P2-8 describes the
    /// dropout entry; this one needs nothing to go wrong at all, just a noisy
    /// room at startup. Bounding the descent cannot help here (the floor never
    /// descends), which is the clearest argument that the escape hatch is
    /// required rather than belt-and-braces.
    #[test]
    fn a_noisy_room_at_startup_is_recovered_by_the_forced_cut_reseed() {
        let mut ep = Endpointer::new(500.0, 100.0);
        let ambient = 0.05; // above the opening threshold of 0.01 * 3

        for _ in 0..200 {
            assert_eq!(
                ep.push(ambient, 20.0),
                VadEvent::SpeechContinues,
                "precondition: a room this noisy reads as speech from the start"
            );
        }
        assert_eq!(
            ep.noise_floor(),
            0.01,
            "precondition: the floor never adapted, because nothing was ever \
             classified as non-speech"
        );

        // This is what `handle_audio_inbound` does once the utterance passes
        // `max_utterance_secs` without endpointing -- the symptom of the latch.
        ep.reseed_noise_floor(ambient);

        for _ in 0..50 {
            assert_eq!(ep.push(ambient, 20.0), VadEvent::Silence);
        }
        assert_eq!(ep.push(0.3, 20.0), VadEvent::SpeechContinues);
    }

    /// The escape hatch. Bounding the descent stops a *single* chunk from
    /// latching the floor, but cannot stop a genuinely long quiet stretch
    /// followed by the noise returning -- the floor legitimately follows the
    /// room down, then the room comes back and every chunk reads as voiced.
    /// `reseed_noise_floor` is what the forced cut calls to break that, and it
    /// must restore a detector that can tell noise from speech again.
    #[test]
    fn reseeding_recovers_a_detector_that_latched_onto_the_room() {
        let mut ep = Endpointer::new(500.0, 100.0);
        // Drive it into the latch directly: a long true silence drops the
        // floor far below the level the room returns to.
        for _ in 0..2000 {
            ep.push(0.0, 20.0);
        }
        let ambient = 0.05;
        // The room comes back. Everything now reads as voiced, and the floor
        // can never recover on its own.
        for _ in 0..200 {
            ep.push(ambient, 20.0);
        }
        assert_eq!(
            ep.push(ambient, 20.0),
            VadEvent::SpeechContinues,
            "precondition: the detector is latched onto room noise"
        );

        ep.reseed_noise_floor(ambient);

        for _ in 0..50 {
            assert_eq!(
                ep.push(ambient, 20.0),
                VadEvent::Silence,
                "reseeding did not restore the ability to hear the room as quiet"
            );
        }
        assert_eq!(ep.push(0.3, 20.0), VadEvent::SpeechContinues);
    }

    #[test]
    fn endpointer_emits_endpoint_after_trailing_silence() {
        let mut ep = Endpointer::new(500.0, 100.0);
        // Establish a quiet floor.
        for _ in 0..20 {
            ep.push(0.001, 20.0);
        }
        // Speech.
        for _ in 0..10 {
            assert_eq!(ep.push(0.2, 20.0), VadEvent::SpeechContinues);
        }
        // Trailing silence shorter than the endpoint window keeps it open.
        assert_eq!(ep.push(0.001, 100.0), VadEvent::SpeechContinues);
        // Crossing the window closes the utterance exactly once.
        assert_eq!(ep.push(0.001, 500.0), VadEvent::Endpoint);
        assert_eq!(ep.push(0.001, 500.0), VadEvent::Silence);
    }

    #[test]
    fn endpointer_rejects_short_blip_as_noise() {
        let mut ep = Endpointer::new(300.0, 200.0);
        for _ in 0..20 {
            ep.push(0.001, 20.0);
        }
        // A 40ms blip is below min_speech_ms, so it must not produce an utterance.
        ep.push(0.3, 20.0);
        ep.push(0.3, 20.0);
        assert_eq!(ep.push(0.001, 400.0), VadEvent::Silence);
    }

    #[test]
    fn short_blip_is_never_confirmed_speech() {
        let mut ep = Endpointer::new(300.0, 200.0);
        for _ in 0..20 {
            ep.push(0.001, 20.0);
        }
        assert!(!ep.speech_confirmed(), "silence is not speech");

        // A 40ms blip still returns SpeechContinues — the utterance buffer has to
        // start filling at onset or speech gets clipped — but it must never be
        // *confirmed*, because that is what gates speculative partial inference and
        // the barge-in audio.stop it can emit. This blip is rejected as noise below,
        // so anything that acted on it would have interrupted the agent for nothing.
        assert_eq!(ep.push(0.3, 20.0), VadEvent::SpeechContinues);
        assert_eq!(ep.push(0.3, 20.0), VadEvent::SpeechContinues);
        assert!(
            !ep.speech_confirmed(),
            "a 40ms blip must not gate partial inference"
        );
        assert_eq!(ep.push(0.001, 400.0), VadEvent::Silence);
    }

    #[test]
    fn speech_is_confirmed_once_min_speech_ms_accumulates() {
        let mut ep = Endpointer::new(300.0, 200.0);
        for _ in 0..20 {
            ep.push(0.001, 20.0);
        }
        // 180ms of voiced audio: still under the 200ms threshold.
        for _ in 0..9 {
            ep.push(0.3, 20.0);
        }
        assert!(!ep.speech_confirmed(), "180ms is below min_speech_ms");

        // Crossing 200ms confirms it.
        ep.push(0.3, 20.0);
        assert!(ep.speech_confirmed(), "200ms should be confirmed speech");

        // Confirmation survives the trailing-silence window so partials keep flowing
        // until the utterance actually closes.
        ep.push(0.001, 100.0);
        assert!(ep.speech_confirmed(), "still open during trailing silence");

        // Closing the utterance clears it.
        assert_eq!(ep.push(0.001, 400.0), VadEvent::Endpoint);
        assert!(!ep.speech_confirmed(), "closed utterance is not speech");
    }
}
