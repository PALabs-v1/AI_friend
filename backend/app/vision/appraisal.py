"""
Visual Appraisal Service — VLM Multimodal Integration.

Converts raw image frames into semantic descriptions via a Vision-Language Model.
Rate-limited and cached to prevent excessive VLM calls on a laptop GPU.
"""

import logging
import time

logger = logging.getLogger(__name__)


class VisualAppraisalService:
    """
    Converts raw base64-encoded frames into natural language descriptions
    using a lightweight VLM (moondream, llava, etc.) via Ollama.

    Design principles:
    - Stateless: just converts images to text
    - Rate-limited: respects VLM_APPRAISAL_INTERVAL
    - Fault-tolerant: returns cached description on VLM failure
    """

    def __init__(
        self,
        ollama_client,
        model: str = "moondream",
        interval: float = 5.0,
        prompt: str = "Describe what you see in this image briefly. Focus on what the user is doing.",
        breaker_failure_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
    ):
        self.llm = ollama_client
        self.model = model
        self.interval = interval
        self.prompt = prompt

        self._last_description: str = ""
        self._last_appraisal_time: float = 0.0
        self._last_visual_vector: list[float] | None = None
        self._habituation_disabled_logged = False

        # P3-1: reuses the same delta this class already computes for
        # habituation (`appraise()` below), rather than a second novelty
        # computation, as the salience signal for whether a frame is worth
        # persisting as a visual memory. True whenever the frame was NOT
        # skipped by habituation -- the first frame ever (nothing to diff
        # against yet) and a frame where continuity-preserving downsampling
        # is unavailable both default True, since "cannot tell" must not
        # silently suppress storage the way it must not silently suppress a
        # VLM call.
        self.last_frame_was_novel: bool = True

        # M3-R3: without this, a down VLM (or Ollama, or a model that was
        # never pulled) got retried every capture tick with a full base64
        # frame -- no backoff. Modeled on the Rust CircuitBreaker
        # (crates/voice-agent/src/main.rs): consecutive_failures crosses
        # breaker_failure_threshold -> opened_at is set; allow_request()
        # stays False until breaker_cooldown_s has elapsed, then the next
        # call is a half-open trial whose own result decides close-vs-reopen.
        # Single-consumer (only `appraise`, called sequentially from
        # VisionAgent's one capture loop), so no lock is needed -- same
        # reasoning the Rust breaker documents for its atomics.
        self._breaker_failure_threshold = max(1, breaker_failure_threshold)
        self._breaker_cooldown_s = breaker_cooldown_s
        self._consecutive_failures = 0
        self._breaker_opened_at: float = 0.0

    def _breaker_allow_request(self) -> bool:
        if self._breaker_opened_at == 0.0:
            return True
        return (time.time() - self._breaker_opened_at) >= self._breaker_cooldown_s

    def _breaker_record_success(self) -> None:
        self._consecutive_failures = 0
        self._breaker_opened_at = 0.0

    def _breaker_record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_failure_threshold:
            self._breaker_opened_at = time.time()

    def should_appraise(self) -> bool:
        """Check if enough time has elapsed since the last VLM call."""
        return (time.time() - self._last_appraisal_time) >= self.interval

    def _compute_visual_vector(self, frame_b64: str) -> list[float] | None:
        """Convert base64 JPEG frame to a downsampled 16x16 grayscale vector.

        Returns None when no perceptually-continuous downsampling path is
        available, so the caller can disable habituation explicitly rather
        than feed it a discontinuous vector (M3-A9). A SHA-256 hash of the
        raw bytes -- the previous fallback -- hashes two near-identical
        frames to unrelated vectors, so the habituation delta always clears
        threshold and the VLM-call cap it exists to provide never engages: a
        fallback that reads as graceful degradation while silently removing
        the very thing it was supposed to degrade gracefully.
        """
        import base64

        try:
            jpeg_bytes = base64.b64decode(frame_b64)
        except Exception:
            return None

        try:
            import cv2
            import numpy as np

            img = cv2.imdecode(
                np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
            )
            if img is not None:
                resized = cv2.resize(img, (16, 16))
                return (resized.flatten().astype(float) / 255.0).tolist()
        except Exception as e:
            logger.debug(
                "[VisualAppraisal] OpenCV downsampling failed, trying PIL: %s", e
            )

        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(jpeg_bytes)).convert("L").resize((16, 16))
            # get_flattened_data (Pillow >= 12.3, the pinned floor) replaces
            # getdata, which is deprecated and removed in Pillow 14.
            return [px / 255.0 for px in img.get_flattened_data()]
        except Exception as e:
            logger.debug(
                "[VisualAppraisal] PIL downsampling failed too, no continuity-"
                "preserving path left: %s",
                e,
            )

        return None

    async def appraise(self, frame_b64: str) -> str:
        """
        Analyze a frame and return a semantic description.

        Returns the cached description if:
        - The rate limit hasn't elapsed
        - The sensory habituation delta is below the threshold
        - The VLM call fails
        """
        if not self.should_appraise():
            self.last_frame_was_novel = False
            return self._last_description

        # Reset before evaluating this frame; the habituation-bypass branch
        # below is the only place that turns it False.
        self.last_frame_was_novel = True

        # Compute delta to evaluate sensory habituation
        current_vector = self._compute_visual_vector(frame_b64)
        if current_vector is None:
            if not self._habituation_disabled_logged:
                logger.warning(
                    "[VisualAppraisal] No perceptually-continuous frame "
                    "downsampling available (cv2 and PIL both failed); "
                    "sensory habituation is disabled and every appraisal "
                    "tick will call the VLM uncapped."
                )
                self._habituation_disabled_logged = True
        elif self._last_visual_vector is not None:
            try:
                import cognitive_rust

                delta = cognitive_rust.compute_vector_delta(
                    self._last_visual_vector, current_vector
                )
            except ImportError:
                import numpy as np

                v1 = np.array(self._last_visual_vector)
                v2 = np.array(current_vector)
                delta = float(np.sum((v1 - v2) ** 2) / len(v1))

            from ..config import Config

            threshold = getattr(Config, "VLM_HABITUATION_THRESHOLD", 0.005)
            if delta < threshold:
                logger.info(
                    "[VisualAppraisal] Sensory Habituation: Delta (%.6f) < Threshold (%.6f). Bypassing VLM call.",
                    delta,
                    threshold,
                )
                self._last_visual_vector = current_vector
                self._last_appraisal_time = time.time()
                self.last_frame_was_novel = False
                return self._last_description

        if not self._breaker_allow_request():
            self.last_frame_was_novel = False
            logger.debug(
                "[VisualAppraisal] Circuit breaker open (%d consecutive failures); "
                "skipping VLM call, using cache.",
                self._consecutive_failures,
            )
            return self._last_description

        try:
            description = await self.llm.describe_image(
                image_b64=frame_b64,
                prompt=self.prompt,
                model=self.model,
            )

            if description:
                self._breaker_record_success()
                self._last_description = description
                self._last_visual_vector = current_vector
                self._last_appraisal_time = time.time()
                logger.info(
                    "[VisualAppraisal] VLM description (%.0fch): %s",
                    len(description),
                    description[:80],
                )
            elif description == "":
                # A successful call with nothing worth describing (H8) is a
                # real observation, not a fault: advance the habituation
                # vector/timestamp so a persistently quiet scene doesn't keep
                # re-triggering VLM calls every tick the way a genuine
                # pipeline failure should (below).
                self._breaker_record_success()
                self._last_visual_vector = current_vector
                self._last_appraisal_time = time.time()
                # The VLM observed no description. If a previous cached
                # description exists, returning it must not make stale
                # content look like a novel frame downstream.
                self.last_frame_was_novel = False
                logger.debug(
                    "[VisualAppraisal] VLM confirmed a quiet scene, using cache."
                )
            else:
                # description is None: the call itself failed. Deliberately
                # does NOT update the habituation vector/timestamp, so the
                # next tick retries the VLM rather than treating this frame
                # as an observed (quiet) baseline -- until the breaker opens.
                self._breaker_record_failure()
                self.last_frame_was_novel = False
                logger.warning("[VisualAppraisal] VLM pipeline failure, using cache.")

        except Exception as e:
            self._breaker_record_failure()
            self.last_frame_was_novel = False
            logger.error(
                "[VisualAppraisal] VLM appraisal failed: %s. Using cached description.",
                e,
            )

        return self._last_description

    @property
    def last_description(self) -> str:
        """Return the most recent cached description."""
        return self._last_description
