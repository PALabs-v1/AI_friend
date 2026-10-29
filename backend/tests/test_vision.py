"""
Test suite for the Vision Intelligence Agent and Visual Appraisal Service.
Validates VLM appraisals, rate-limiting, source switching controls, and NATS emissions.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts import FacialReflexEvent, Topics, VisionDescription
from app.llm.model_roles import ModelRole
from app.vision.agent import VisionAgent
from app.vision.appraisal import VisualAppraisalService


def _real_jpeg_frame_b64(color=(120, 60, 200)) -> str:
    """A genuinely decodable JPEG frame, base64-encoded.

    M3-A9 removed the SHA-256 fallback that used to turn *any* base64
    string into a stand-in vector; `_compute_visual_vector` now needs bytes
    a real decoder (cv2 or PIL) can open, so tests exercising habituation
    need real image bytes rather than arbitrary strings.
    """
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.fixture
def mock_ollama_client():
    client = MagicMock()
    client.describe_image = AsyncMock(return_value="A developer coding on a laptop.")
    return client


@pytest.fixture
def mock_appraisal_service(mock_ollama_client):
    service = VisualAppraisalService(
        ollama_client=mock_ollama_client,
        model="moondream",
        interval=1.0,
        prompt="Describe what you see.",
    )
    return service


class TestVisualAppraisalService:
    @pytest.mark.asyncio
    async def test_appraisal_initial_call_triggers_vlm(
        self, mock_appraisal_service, mock_ollama_client
    ):
        # Initial call should trigger the VLM describe_image call
        desc = await mock_appraisal_service.appraise("fake_base64_string")
        assert desc == "A developer coding on a laptop."
        mock_ollama_client.describe_image.assert_awaited_once_with(
            image_b64="fake_base64_string",
            prompt="Describe what you see.",
            model="moondream",
            role=ModelRole.INTERPRETATION,
        )

    @pytest.mark.asyncio
    async def test_appraisal_rate_limiting_returns_cached_description(
        self, mock_appraisal_service, mock_ollama_client
    ):
        # Call it once to populate the cache
        desc1 = await mock_appraisal_service.appraise("fake_base64_string")
        assert desc1 == "A developer coding on a laptop."

        # Call it again immediately; it should respect rate limiting and NOT trigger a second VLM call
        mock_ollama_client.describe_image.reset_mock()
        desc2 = await mock_appraisal_service.appraise("another_fake_base64_string")

        # Returns cached value from first call
        assert desc2 == "A developer coding on a laptop."
        mock_ollama_client.describe_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_appraisal_error_tolerance_returns_cache(
        self, mock_appraisal_service, mock_ollama_client
    ):
        # Call once successfully to establish cache
        await mock_appraisal_service.appraise("fake_base64_string")

        # Force next VLM call to raise an exception
        mock_ollama_client.describe_image = AsyncMock(
            side_effect=Exception("Ollama Connection Timeout")
        )

        # Advance time beyond the appraisal interval to trigger a new VLM call
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0

        # Call service; it should gracefully catch exception and return last cached value
        desc = await mock_appraisal_service.appraise("new_frame_b64")
        assert desc == "A developer coding on a laptop."
        assert mock_appraisal_service.last_frame_was_novel is False

    @pytest.mark.asyncio
    async def test_empty_vlm_result_marks_cached_description_non_novel(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """An empty observation must not republish the previous cache as new."""
        await mock_appraisal_service.appraise("first_frame")

        mock_ollama_client.describe_image = AsyncMock(return_value="")
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0

        desc = await mock_appraisal_service.appraise("quiet_frame")

        assert desc == "A developer coding on a laptop."
        assert mock_appraisal_service.last_frame_was_novel is False

    @pytest.mark.asyncio
    async def test_vlm_pipeline_failure_does_not_advance_habituation_baseline(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """H8: `describe_image` returning `None` (the call itself failed)
        must NOT be treated as an observed baseline - the next tick should
        still retry the VLM rather than skip it via sensory habituation,
        which is what advancing `_last_visual_vector`/`_last_appraisal_time`
        here would cause.
        """
        mock_ollama_client.describe_image = AsyncMock(return_value=None)
        forced_last_appraisal_time = time.time() - 2.0
        mock_appraisal_service._last_appraisal_time = forced_last_appraisal_time

        desc = await mock_appraisal_service.appraise("new_frame_b64")

        assert desc == ""  # falls back to the (empty) initial cache
        assert mock_appraisal_service._last_visual_vector is None
        # Unchanged from what we forced it to, not bumped to "now" - a bump
        # here would let a subsequent frame's habituation check treat this
        # failed call as an observed baseline instead of retrying the VLM.
        assert mock_appraisal_service._last_appraisal_time == forced_last_appraisal_time

    @pytest.mark.asyncio
    async def test_vlm_confirmed_quiet_scene_advances_habituation_baseline(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """H8: `describe_image` returning `""` (a successful call that found
        nothing worth describing) IS a real observation and should advance
        the habituation baseline, unlike a `None` pipeline failure above.
        """
        mock_ollama_client.describe_image = AsyncMock(return_value="")
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0

        desc = await mock_appraisal_service.appraise(_real_jpeg_frame_b64())

        assert desc == ""
        assert mock_appraisal_service._last_visual_vector is not None
        assert mock_appraisal_service._last_appraisal_time > 0.0

    @pytest.mark.asyncio
    async def test_sensory_habituation_bypasses_vlm_if_below_threshold(
        self, mock_appraisal_service, mock_ollama_client
    ):
        # 1. Establish the initial frame and cache
        frame_b64 = _real_jpeg_frame_b64()

        desc1 = await mock_appraisal_service.appraise(frame_b64)
        assert desc1 == "A developer coding on a laptop."
        assert mock_ollama_client.describe_image.call_count == 1

        # Reset VLM mock and advance time beyond interval
        mock_ollama_client.describe_image.reset_mock()
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0

        # 2. Call again with the same base64 frame (delta will be 0.0 < VLM_HABITUATION_THRESHOLD)
        desc2 = await mock_appraisal_service.appraise(frame_b64)
        assert desc2 == "A developer coding on a laptop."
        # Should bypass describe_image call completely due to habituation
        mock_ollama_client.describe_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_habituation_bypass_marks_the_frame_not_novel(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """P3-1: `last_frame_was_novel` reuses this same habituation delta as
        the salience signal for visual episodic memory -- a habituated
        (skipped) frame must be flagged not-novel, not left at its default."""
        frame_b64 = _real_jpeg_frame_b64()

        await mock_appraisal_service.appraise(frame_b64)
        assert mock_appraisal_service.last_frame_was_novel is True

        mock_ollama_client.describe_image.reset_mock()
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise(frame_b64)

        assert mock_appraisal_service.last_frame_was_novel is False

    @pytest.mark.asyncio
    async def test_novel_frame_after_a_habituated_one_resets_the_flag(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """The flag must not stick at False forever once a scene actually
        changes -- otherwise every visual memory after the first habituated
        frame would be silently suppressed."""
        same_frame = _real_jpeg_frame_b64()
        different_frame = _real_jpeg_frame_b64(color=(10, 200, 30))

        await mock_appraisal_service.appraise(same_frame)
        mock_ollama_client.describe_image.reset_mock()
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise(same_frame)
        assert mock_appraisal_service.last_frame_was_novel is False

        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise(different_frame)
        assert mock_appraisal_service.last_frame_was_novel is True

    @pytest.mark.asyncio
    async def test_breaker_opens_after_consecutive_failures_and_stops_calling_vlm(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """M3-R3: a down VLM used to be retried every tick with a full
        frame, with no backoff. After breaker_failure_threshold consecutive
        pipeline failures, the VLM must not be called again until the
        cooldown elapses."""
        mock_appraisal_service._breaker_failure_threshold = 2
        mock_ollama_client.describe_image = AsyncMock(return_value=None)

        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise("f1")
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise("f2")
        assert mock_appraisal_service._consecutive_failures == 2
        assert mock_appraisal_service._breaker_opened_at > 0.0

        mock_ollama_client.describe_image.reset_mock()
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        desc = await mock_appraisal_service.appraise("f3")

        mock_ollama_client.describe_image.assert_not_awaited()
        assert desc == ""
        assert mock_appraisal_service.last_frame_was_novel is False

    @pytest.mark.asyncio
    async def test_breaker_half_open_trial_after_cooldown_recovers_on_success(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """Once the cooldown has elapsed, the next call is a real attempt
        (a half-open trial) - a success must close the breaker rather than
        leaving it permanently open."""
        mock_appraisal_service._breaker_failure_threshold = 1
        mock_ollama_client.describe_image = AsyncMock(return_value=None)
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise("f1")
        assert mock_appraisal_service._breaker_opened_at > 0.0

        # Simulate the cooldown window having elapsed.
        mock_appraisal_service._breaker_cooldown_s = 0.01
        mock_appraisal_service._breaker_opened_at = time.time() - 1.0
        mock_ollama_client.describe_image = AsyncMock(return_value="Recovered scene.")
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0

        desc = await mock_appraisal_service.appraise("f2")

        mock_ollama_client.describe_image.assert_awaited_once()
        assert desc == "Recovered scene."
        assert mock_appraisal_service._consecutive_failures == 0
        assert mock_appraisal_service._breaker_opened_at == 0.0

    @pytest.mark.asyncio
    async def test_visual_vector_returns_none_when_undecodable(
        self, mock_appraisal_service
    ):
        """M3-A9: when neither cv2 nor PIL can decode the frame, the vector
        must come back None rather than a SHA-256 hash of the raw bytes --
        two near-identical undecodable frames would hash to unrelated
        vectors, so a habituation delta computed against them always clears
        threshold and never actually caps the VLM."""
        import base64

        garbage_b64 = base64.b64encode(b"not a real jpeg").decode("utf-8")
        assert mock_appraisal_service._compute_visual_vector(garbage_b64) is None

    @pytest.mark.asyncio
    async def test_undecodable_frames_disable_habituation_and_log_once(
        self, mock_appraisal_service, mock_ollama_client, caplog
    ):
        """With no continuity-preserving vector available, habituation must
        not silently no-op -- it disables explicitly (VLM stays uncapped)
        and says so, once, rather than spamming a warning every tick."""
        import base64
        import logging

        garbage_b64 = base64.b64encode(b"not a real jpeg").decode("utf-8")

        with caplog.at_level(logging.WARNING, logger="app.vision.appraisal"):
            await mock_appraisal_service.appraise(garbage_b64)
            mock_appraisal_service._last_appraisal_time = time.time() - 2.0
            await mock_appraisal_service.appraise(garbage_b64)

        # Habituation never engaged: both calls hit the VLM.
        assert mock_ollama_client.describe_image.await_count == 2
        degradation_warnings = [
            r for r in caplog.records if "habituation is disabled" in r.message
        ]
        assert len(degradation_warnings) == 1

    @pytest.mark.asyncio
    async def test_breaker_success_resets_failure_count_below_threshold(
        self, mock_appraisal_service, mock_ollama_client
    ):
        """A transient failure followed by a success must not accumulate
        toward the threshold across unrelated errors."""
        mock_appraisal_service._breaker_failure_threshold = 3
        mock_ollama_client.describe_image = AsyncMock(return_value=None)
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise("f1")
        assert mock_appraisal_service._consecutive_failures == 1

        mock_ollama_client.describe_image = AsyncMock(return_value="Back to normal.")
        mock_appraisal_service._last_appraisal_time = time.time() - 2.0
        await mock_appraisal_service.appraise("f2")

        assert mock_appraisal_service._consecutive_failures == 0
        assert mock_appraisal_service._breaker_opened_at == 0.0


class TestVisionAgent:
    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_vision_agent_initialization(self, mock_camera_cls, mock_screen_cls):
        agent = VisionAgent()
        assert agent.source == "screen"
        assert agent.vlm_enabled is True
        assert agent.running is False

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_vision_agent_source_switching(
        self, mock_camera_cls, mock_screen_cls
    ):
        agent = VisionAgent()

        # Initial source defaults to screen
        assert agent.source == "screen"

        # Switch to camera
        await agent._handle_control({"source": "camera"})
        assert agent.source == "camera"

        # Switch to screen
        await agent._handle_control({"source": "screen"})
        assert agent.source == "screen"

        # Switch to invalid source; should ignore
        await agent._handle_control({"source": "invalid_source"})
        assert agent.source == "screen"

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_vision_agent_run_appraisal_publishes_event(
        self, mock_camera_cls, mock_screen_cls
    ):
        agent = VisionAgent()
        agent.publish = AsyncMock()

        # Mock appraisal service to return a description
        mock_appraisal = MagicMock()
        mock_appraisal.should_appraise.return_value = True
        mock_appraisal.appraise = AsyncMock(
            return_value="A clean glass desk with three monitors."
        )
        mock_appraisal.last_frame_was_novel = True
        agent.appraisal = mock_appraisal

        # We pass a simple valid base64 string for a tiny 1x1 black png or blank bytes
        import base64

        blank_frame_b64 = base64.b64encode(b"\x00" * 100).decode("utf-8")
        await agent._run_appraisal(blank_frame_b64)

        # Verify appraisal was invoked and output published to Topics.VISION_DESCRIPTION
        mock_appraisal.appraise.assert_awaited_once_with(blank_frame_b64)
        agent.publish.assert_awaited_once()

        topic, payload = agent.publish.await_args.args
        assert topic == Topics.VISION_DESCRIPTION

        # Verify schema compliance and user_distance presence
        msg = VisionDescription.model_validate(payload)
        assert msg.description == "A clean glass desk with three monitors."
        assert msg.source == "screen"
        assert msg.user_distance is not None
        assert msg.user_distance == 1.0  # Fallback since frame is invalid/blank
        assert msg.is_novel is True

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_appraisal_suspended_while_turn_in_flight(
        self, mock_camera_cls, mock_screen_cls
    ):
        """P1-7: the VLM and the conversational LLM contend on one Ollama
        endpoint (MEASURED -44%/-47% decode rate in audit/HARDWARE.md).
        While a cognitive turn is in flight, appraisal must not call the
        VLM at all - not even a rate-limited one."""
        import base64

        agent = VisionAgent()
        agent.publish = AsyncMock()
        mock_appraisal = MagicMock()
        mock_appraisal.should_appraise.return_value = True
        mock_appraisal.appraise = AsyncMock(return_value="Should not run.")
        agent.appraisal = mock_appraisal

        await agent._on_chat_input({})
        assert agent._turn_in_flight is True

        blank_frame_b64 = base64.b64encode(b"\x00" * 100).decode("utf-8")
        await agent._run_appraisal(blank_frame_b64)

        mock_appraisal.appraise.assert_not_awaited()
        agent.publish.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_appraisal_resumes_once_turn_output_is_done(
        self, mock_camera_cls, mock_screen_cls
    ):
        agent = VisionAgent()

        await agent._on_chat_input({})
        assert agent._turn_in_flight is True

        # A non-final chunk must not close the turn.
        await agent._on_chat_output({"done": False, "content": "partial"})
        assert agent._turn_in_flight is True

        await agent._on_chat_output({"done": True})
        assert agent._turn_in_flight is False

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_turn_watchdog_resumes_appraisal_if_done_never_arrives(
        self, mock_camera_cls, mock_screen_cls
    ):
        """A crashed brain_agent or a dropped chat.output must not blind
        vision forever - bounded by LLM_STREAM_MAX_SECONDS, the same
        deadline a turn itself is allowed to run for."""
        from app.config import Config

        agent = VisionAgent()
        agent._turn_in_flight = True
        agent._turn_started_at = time.time() - (Config.LLM_STREAM_MAX_SECONDS + 1)

        assert agent._is_turn_in_flight() is False
        assert agent._turn_in_flight is False

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_turn_watchdog_does_not_fire_before_deadline(
        self, mock_camera_cls, mock_screen_cls
    ):
        agent = VisionAgent()
        agent._turn_in_flight = True
        agent._turn_started_at = time.time()

        assert agent._is_turn_in_flight() is True

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_appraisal_not_suspended_when_flag_disabled(
        self, mock_camera_cls, mock_screen_cls, monkeypatch
    ):
        """VISION_SUSPEND_DURING_TURN=False must fully restore the
        pre-P1-7 behavior - a stuck or unwanted turn-in-flight flag must
        not silently gate appraisal when the feature is off."""
        import base64

        from app import config as config_module

        monkeypatch.setattr(
            config_module.config_instance, "VISION_SUSPEND_DURING_TURN", False
        )

        agent = VisionAgent()
        agent.publish = AsyncMock()
        agent._turn_in_flight = True  # simulate a stuck flag
        mock_appraisal = MagicMock()
        mock_appraisal.should_appraise.return_value = True
        mock_appraisal.appraise = AsyncMock(return_value="Seen anyway.")
        mock_appraisal.last_frame_was_novel = True
        agent.appraisal = mock_appraisal

        blank_frame_b64 = base64.b64encode(b"\x00" * 100).decode("utf-8")
        await agent._run_appraisal(blank_frame_b64)

        mock_appraisal.appraise.assert_awaited_once()
        agent.publish.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_face_cascade_is_built_once_not_per_call(
        self, mock_camera_cls, mock_screen_cls
    ):
        """M3-P6: the cascade used to be reconstructed from XML on every
        single call inside the hot capture loop. It must be built once, at
        construction, and reused."""
        with patch("app.vision.agent.cv2") as mock_cv2, patch("app.vision.agent.np"):
            mock_cv2.data.haarcascades = "/fake/"
            cascade = MagicMock()
            cascade.detectMultiScale.return_value = []
            mock_cv2.CascadeClassifier.return_value = cascade
            mock_cv2.imdecode.return_value = MagicMock(shape=(480, 640, 3))

            agent = VisionAgent()
            assert mock_cv2.CascadeClassifier.call_count == 1

            agent._calculate_user_distance("Zg==")
            agent._calculate_user_distance("Zg==")

            # Still exactly one construction across both calls.
            assert mock_cv2.CascadeClassifier.call_count == 1
            assert cascade.detectMultiScale.call_count == 2

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_distance_uses_calibrated_focal_length_formula(
        self, mock_camera_cls, mock_screen_cls
    ):
        """M3-A10: distance must come from the pinhole formula
        (FACE_WIDTH_M * focal_px) / face_width_px, not a focal-length-free
        ratio that implicitly assumed focal_px == image_width."""
        from app.config import Config
        from app.vision.agent import ASSUMED_FACE_WIDTH_M

        with (
            patch("app.vision.agent.cv2") as mock_cv2,
            patch("app.vision.agent.np") as mock_np,
        ):
            mock_cv2.data.haarcascades = "/fake/"
            cascade = MagicMock()
            face_width_px = 100
            cascade.detectMultiScale.return_value = [(0, 0, face_width_px, 100)]
            mock_cv2.CascadeClassifier.return_value = cascade
            image_width = Config.VISION_FOCAL_REFERENCE_WIDTH_PX
            mock_cv2.imdecode.return_value = MagicMock(shape=(480, image_width, 3))
            mock_np.clip.side_effect = lambda v, lo, hi: max(lo, min(hi, v))

            agent = VisionAgent()
            distance = agent._calculate_user_distance("Zg==")

            expected = (ASSUMED_FACE_WIDTH_M * Config.VISION_FOCAL_PX) / face_width_px
            assert distance == pytest.approx(expected)

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_run_appraisal_offloads_distance_calc_off_the_event_loop(
        self, mock_camera_cls, mock_screen_cls
    ):
        """M3-P6: detectMultiScale is a blocking OpenCV call; it must not
        run inline on the event loop the way the rest of _run_appraisal
        does."""
        import base64

        agent = VisionAgent()
        agent.publish = AsyncMock()
        mock_appraisal = MagicMock()
        mock_appraisal.should_appraise.return_value = True
        mock_appraisal.appraise = AsyncMock(return_value="A description.")
        mock_appraisal.last_frame_was_novel = True
        agent.appraisal = mock_appraisal
        agent._calculate_user_distance = MagicMock(return_value=1.5)

        with patch(
            "app.vision.agent.asyncio.to_thread", new_callable=AsyncMock
        ) as mock_to_thread:
            mock_to_thread.return_value = 1.5
            blank_frame_b64 = base64.b64encode(b"\x00" * 100).decode("utf-8")
            await agent._run_appraisal(blank_frame_b64)

            mock_to_thread.assert_awaited_once_with(
                agent._calculate_user_distance, blank_frame_b64
            )

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_turn_tracking_subscribes_for_new_messages_only(
        self, mock_camera_cls, mock_screen_cls
    ):
        """These two subscriptions are liveness signals, not work items. On
        JetStream's "all" default a fresh durable replays the whole retained
        chat history at startup - and chat.output carries one message per
        response chunk - so every vision restart would re-walk the entire
        conversation and end up suspended by a turn that finished hours ago,
        blind until the watchdog fired. Every other subscription in the mesh
        names a policy explicitly; these must too."""
        agent = VisionAgent()
        agent.connect = AsyncMock()
        agent.subscribe = AsyncMock()
        agent.preflight = MagicMock(return_value=False)
        agent._capture_loop = AsyncMock()

        await agent.start()

        policies = {
            call.args[0]: call.kwargs.get("deliver_policy")
            for call in agent.subscribe.await_args_list
        }
        assert policies[Topics.CHAT_INPUT] == "new"
        assert policies[Topics.CHAT_OUTPUT] == "new"


class _FakeBlendshapeCategory:
    def __init__(self, category_name: str, score: float):
        self.category_name = category_name
        self.score = score


class _FakeFaceLandmarkerResult:
    """Stands in for `mediapipe.tasks.python.vision.FaceLandmarkerResult` --
    `reflex.py::score_blendshapes` only ever needs `{name: score}`, but the
    real object nests `Category` instances one list of lists deep."""

    def __init__(self, blendshapes: dict[str, float] | None):
        if blendshapes is None:
            self.face_blendshapes = []
        else:
            self.face_blendshapes = [
                [
                    _FakeBlendshapeCategory(name, score)
                    for name, score in blendshapes.items()
                ]
            ]


class TestVisionAgentFacialReflex:
    """Bucket 13 (voice remediation Phase 3): the CPU-only facial reflex
    channel's live wiring -- `reflex.py`'s scoring logic already has its own
    dedicated unit tests (`test_facial_reflex.py`) with no MediaPipe
    involved at all; these test the part that logic can't reach on its
    own -- decoding a real frame, calling a (mocked) Face Landmarker, and
    publishing the result to the mesh.
    """

    @pytest.mark.asyncio
    @patch("app.vision.agent.mp")
    @patch("app.vision.agent.cv2")
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_publishes_a_facial_reflex_event_for_a_detected_smile(
        self, mock_camera_cls, mock_screen_cls, mock_cv2, mock_mp
    ):
        """cv2/mp are mocked here (not just the landmarker) because CI never
        installs opencv-python/mediapipe -- requirements-ai.txt is optional
        and only requirements-dev.txt runs in CI -- so both are genuinely
        `None` there. Without this, `cv2.imdecode`/`mp.Image` raise inside
        `_run_facial_reflex`'s own try/except, get silently swallowed, and
        this test passes for the wrong reason (a caught crash) until the
        assertion below catches the missing publish."""
        agent = VisionAgent()
        agent.source = "camera"
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._face_landmarker.detect.return_value = _FakeFaceLandmarkerResult(
            {"mouthSmileLeft": 0.9, "mouthSmileRight": 0.9}
        )

        await agent._run_facial_reflex(_real_jpeg_frame_b64())

        agent.publish.assert_awaited_once()
        topic, payload = agent.publish.await_args.args
        assert topic == Topics.VISION_FACIAL_REFLEX
        event = FacialReflexEvent.model_validate(payload)
        assert event.name == "smile"
        assert event.valence_delta > 0.0
        assert event.source == "camera"

    @pytest.mark.asyncio
    @patch("app.vision.agent.mp")
    @patch("app.vision.agent.cv2")
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_publishes_nothing_when_no_face_is_detected(
        self, mock_camera_cls, mock_screen_cls, mock_cv2, mock_mp
    ):
        agent = VisionAgent()
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._face_landmarker.detect.return_value = _FakeFaceLandmarkerResult(None)

        await agent._run_facial_reflex(_real_jpeg_frame_b64())

        agent.publish.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.vision.agent.mp")
    @patch("app.vision.agent.cv2")
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_publishes_nothing_for_a_calm_face_below_every_threshold(
        self, mock_camera_cls, mock_screen_cls, mock_cv2, mock_mp
    ):
        """A detected face with no expression crossing any of `reflex.py`'s
        thresholds must produce silence, not a zero-delta event -- the same
        absence-guard `apply_facial_reflex` itself already enforces."""
        agent = VisionAgent()
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._face_landmarker.detect.return_value = _FakeFaceLandmarkerResult(
            {"mouthSmileLeft": 0.01, "mouthSmileRight": 0.01, "jawOpen": 0.02}
        )

        await agent._run_facial_reflex(_real_jpeg_frame_b64())

        agent.publish.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_an_undecodable_frame_is_handled_without_raising(
        self, mock_camera_cls, mock_screen_cls
    ):
        """A corrupt or partial frame must degrade to 'nothing published',
        the same posture every other capture-path failure in this agent
        takes -- never propagate out of the capture loop."""
        agent = VisionAgent()
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()

        import base64

        garbage_b64 = base64.b64encode(b"not a jpeg at all").decode("utf-8")

        await agent._run_facial_reflex(garbage_b64)  # must not raise

        agent.publish.assert_not_awaited()
        agent._face_landmarker.detect.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.vision.agent.mp")
    @patch("app.vision.agent.cv2")
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_repeated_onsets_within_the_refractory_window_fire_once(
        self, mock_camera_cls, mock_screen_cls, mock_cv2, mock_mp
    ):
        """The tracker is per-agent-instance state, held across calls -- a
        held smile across several capture ticks must not re-fire until
        `reflex.REFRACTORY_SECONDS` has actually elapsed."""
        agent = VisionAgent()
        agent.source = "camera"
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._face_landmarker.detect.return_value = _FakeFaceLandmarkerResult(
            {"mouthSmileLeft": 0.9, "mouthSmileRight": 0.9}
        )

        frame = _real_jpeg_frame_b64()
        await agent._run_facial_reflex(frame)
        await agent._run_facial_reflex(frame)

        agent.publish.assert_awaited_once()

    # ---------------------------------------------------------------- capture-loop gating

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_capture_loop_runs_facial_reflex_in_camera_mode(
        self, mock_camera_cls, mock_screen_cls
    ):
        agent = VisionAgent(fps=1000.0)  # near-zero sleep so the test is fast
        agent.source = "camera"
        agent.vlm_enabled = False
        agent.running = True
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._run_facial_reflex = AsyncMock()

        def _one_frame_then_stop(*args, **kwargs):
            agent.running = False
            return b"fake-jpeg-bytes"

        agent.camera.capture_frame = MagicMock(side_effect=_one_frame_then_stop)

        await agent._capture_loop()

        agent._run_facial_reflex.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_capture_loop_skips_facial_reflex_in_screen_mode(
        self, mock_camera_cls, mock_screen_cls
    ):
        """A screen capture has no face to score -- must be skipped rather
        than spending CPU on a detection that can never fire."""
        agent = VisionAgent(fps=1000.0)
        agent.source = "screen"
        agent.vlm_enabled = False
        agent.running = True
        agent.publish = AsyncMock()
        agent._face_landmarker = MagicMock()
        agent._run_facial_reflex = AsyncMock()

        def _one_frame_then_stop(*args, **kwargs):
            agent.running = False
            return b"fake-jpeg-bytes"

        agent.screen.capture_frame = MagicMock(side_effect=_one_frame_then_stop)

        await agent._capture_loop()

        agent._run_facial_reflex.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    async def test_capture_loop_skips_facial_reflex_when_no_landmarker_loaded(
        self, mock_camera_cls, mock_screen_cls
    ):
        """mediapipe missing, or the model file not found -- either way
        `_face_landmarker` stays None and the capture loop must not attempt
        to call it."""
        agent = VisionAgent(fps=1000.0)
        agent.source = "camera"
        agent.vlm_enabled = False
        agent.running = True
        agent.publish = AsyncMock()
        agent._face_landmarker = None
        agent._run_facial_reflex = AsyncMock()

        def _one_frame_then_stop(*args, **kwargs):
            agent.running = False
            return b"fake-jpeg-bytes"

        agent.camera.capture_frame = MagicMock(side_effect=_one_frame_then_stop)

        await agent._capture_loop()

        agent._run_facial_reflex.assert_not_awaited()

    # ---------------------------------------------------------------- __init__ wiring

    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    def test_landmarker_stays_none_when_the_model_file_is_missing(
        self, mock_camera_cls, mock_screen_cls, tmp_path, monkeypatch
    ):
        """The model asset (~3.7MB) is gitignored and not fetched by a
        fresh clone -- startup must degrade to 'reflex channel disabled',
        never fail agent construction entirely."""
        from app.config import Config

        missing_path = tmp_path / "does_not_exist.task"
        monkeypatch.setattr(Config, "FACIAL_REFLEX_ENABLED", True)
        monkeypatch.setattr(Config, "FACIAL_REFLEX_MODEL_PATH", str(missing_path))

        agent = VisionAgent(facial_reflex_model_path=missing_path)

        assert agent._face_landmarker is None

    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    def test_landmarker_stays_none_when_the_feature_is_disabled(
        self, mock_camera_cls, mock_screen_cls, tmp_path, monkeypatch
    ):
        from app.config import Config

        model_path = tmp_path / "face_landmarker.task"
        model_path.write_bytes(b"not a real model, just needs to exist")
        monkeypatch.setattr(Config, "FACIAL_REFLEX_MODEL_PATH", str(model_path))
        monkeypatch.setattr(Config, "FACIAL_REFLEX_ENABLED", False)

        agent = VisionAgent(facial_reflex_model_path=model_path)

        assert agent._face_landmarker is None

    @patch("app.vision.agent.mp_vision", None)
    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    def test_landmarker_stays_none_when_mediapipe_is_not_installed(
        self, mock_camera_cls, mock_screen_cls, tmp_path, monkeypatch
    ):
        from app.config import Config

        model_path = tmp_path / "face_landmarker.task"
        model_path.write_bytes(b"not a real model, just needs to exist")
        monkeypatch.setattr(Config, "FACIAL_REFLEX_ENABLED", True)
        monkeypatch.setattr(Config, "FACIAL_REFLEX_MODEL_PATH", str(model_path))

        agent = VisionAgent(facial_reflex_model_path=model_path)

        assert agent._face_landmarker is None

    @patch("app.vision.agent.ScreenLink")
    @patch("app.vision.agent.CameraLink")
    def test_landmarker_is_built_when_the_model_file_exists(
        self, mock_camera_cls, mock_screen_cls, tmp_path, monkeypatch
    ):
        """Wiring only -- does not load a real model (slow, and the real
        asset isn't present in CI). `mp_tasks`/`mp_vision` are replaced
        wholesale, not just `create_from_options`, because CI never installs
        mediapipe at all (it's in requirements-ai.txt, an optional extra;
        only requirements-dev.txt runs in CI) -- both are genuinely `None`
        there, so patching only one attribute off a `None` module would
        itself raise `AttributeError` before the mock ever applied. This
        proves the constructor path is reached with the right arguments,
        not that mediapipe can parse the file."""
        from app.config import Config

        model_path = tmp_path / "face_landmarker.task"
        model_path.write_bytes(b"not a real model, just needs to exist")
        monkeypatch.setattr(Config, "FACIAL_REFLEX_ENABLED", True)
        monkeypatch.setattr(Config, "FACIAL_REFLEX_MODEL_PATH", str(model_path))

        sentinel = MagicMock()
        mock_mp_vision = MagicMock()
        mock_mp_vision.FaceLandmarker.create_from_options.return_value = sentinel
        mock_mp_tasks = MagicMock()
        with (
            patch("app.vision.agent.mp_tasks", mock_mp_tasks),
            patch("app.vision.agent.mp_vision", mock_mp_vision),
        ):
            agent = VisionAgent(facial_reflex_model_path=model_path)

        assert agent._face_landmarker is sentinel
