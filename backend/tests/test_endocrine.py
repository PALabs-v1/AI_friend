"""
Test suite for the Endocrine Somatic System.
Validates cortisol/dopamine derivation from PAD state and
downstream LLM parameter modulation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.state.agent_state import AgentState, StateService


class TestEndocrineProperties:
    """Cortisol and Dopamine are derived hormones, not stored state."""

    def test_cortisol_at_neutral_valence(self):
        """V=0.0 → cortisol = 0.5 (baseline stress)."""
        state = AgentState(mood=0.0)
        assert state.cortisol == pytest.approx(0.5, abs=0.01)

    def test_cortisol_at_positive_valence(self):
        """V=1.0 → cortisol = 0.0 (fully relaxed)."""
        state = AgentState(mood=1.0)
        assert state.cortisol == pytest.approx(0.0, abs=0.01)

    def test_cortisol_at_negative_valence(self):
        """V=-1.0 → cortisol = 1.0 (maximum stress)."""
        state = AgentState(mood=-1.0)
        assert state.cortisol == pytest.approx(1.0, abs=0.01)

    def test_cortisol_clamped_to_bounds(self):
        """Cortisol is always in [0.0, 1.0] even with extreme PAD values."""
        # Valence beyond normal range (shouldn't happen, but defensive)
        state = AgentState(mood=2.0)
        assert 0.0 <= state.cortisol <= 1.0
        state.mood = -2.0
        assert 0.0 <= state.cortisol <= 1.0

    def test_dopamine_at_positive_state(self):
        """V=0.8, Ar=0.7 → dopamine = 0.56 (high reward)."""
        state = AgentState(mood=0.8, energy=0.7)
        assert state.dopamine == pytest.approx(0.56, abs=0.01)

    def test_dopamine_zero_when_negative_valence(self):
        """V=-0.5 → dopamine = 0.0 (no reward signal from negative emotions)."""
        state = AgentState(mood=-0.5, energy=0.9)
        assert state.dopamine == pytest.approx(0.0, abs=0.01)

    def test_dopamine_zero_when_zero_arousal(self):
        """V=1.0, Ar=0.0 → dopamine = 0.0 (reward requires activation energy)."""
        state = AgentState(mood=1.0, energy=0.0)
        assert state.dopamine == pytest.approx(0.0, abs=0.01)

    def test_dopamine_peak(self):
        """V=1.0, Ar=1.0 → dopamine = 1.0 (peak reward)."""
        state = AgentState(mood=1.0, energy=1.0)
        assert state.dopamine == pytest.approx(1.0, abs=0.01)


class TestEndocrineInSnapshot:
    """Ensure cortisol/dopamine propagate through get_context_snapshot."""

    @pytest.fixture
    def state_service(self):
        with patch(
            "app.state.agent_state.StateService.persist_state",
            new_callable=AsyncMock,
        ):
            return StateService(graph_store=None)

    def test_snapshot_contains_endocrine_keys(self, state_service):
        snap = state_service.get_context_snapshot()
        assert "cortisol" in snap
        assert "dopamine" in snap

    def test_snapshot_endocrine_values_match_state(self, state_service):
        state_service.current_state.mood = 0.6
        state_service.current_state.energy = 0.8
        snap = state_service.get_context_snapshot()
        assert snap["cortisol"] == pytest.approx(
            state_service.current_state.cortisol, abs=0.001
        )
        assert snap["dopamine"] == pytest.approx(
            state_service.current_state.dopamine, abs=0.001
        )


class TestEndocrineLLMModulation:
    """Verify that ActionService correctly calculates LLM options from endocrine state."""

    @pytest.fixture
    def action_service(self, mock_llm_service):
        from app.cognitive.action import ActionService

        return ActionService(llm_service=mock_llm_service)

    @pytest.mark.asyncio
    async def test_high_cortisol_lowers_temperature(self, action_service):
        """Stressed agent (cortisol=1.0) → temperature=0.3 (rigid/defensive)."""
        from app.cognitive.decision import ActionPlan

        plan = ActionPlan(
            action_type="RESPOND_CHAT",
            goal="PROTECT",
            payload={
                "message": "test",
                "identity_prompt": "You are a test agent.",
                "emotion_state": "angry",
                "cortisol": 1.0,
                "dopamine": 0.0,
            },
        )

        # Capture the options_override passed to generate_stream
        captured_options = {}

        async def capturing_stream(
            prompt, system=None, model=None, options_override=None, role=None
        ):
            captured_options.update(options_override or {})
            yield "Test response."

        action_service.llm.generate_stream = MagicMock(side_effect=capturing_stream)

        async for _ in action_service.execute(plan):
            pass

        assert captured_options["temperature"] == pytest.approx(0.3, abs=0.01)
        assert captured_options["top_p"] == pytest.approx(0.70, abs=0.01)

    @pytest.mark.asyncio
    async def test_relaxed_happy_agent_raises_exploration(self, action_service):
        """Happy+energized agent (cortisol=0.0, dopamine=1.0) → temp=0.9, top_p=0.95."""
        from app.cognitive.decision import ActionPlan

        plan = ActionPlan(
            action_type="RESPOND_CHAT",
            goal="ENGAGE",
            payload={
                "message": "test",
                "identity_prompt": "You are a test agent.",
                "emotion_state": "excited",
                "cortisol": 0.0,
                "dopamine": 1.0,
            },
        )

        captured_options = {}

        async def capturing_stream(
            prompt, system=None, model=None, options_override=None, role=None
        ):
            captured_options.update(options_override or {})
            yield "Awesome response!"

        action_service.llm.generate_stream = MagicMock(side_effect=capturing_stream)

        async for _ in action_service.execute(plan):
            pass

        assert captured_options["temperature"] == pytest.approx(0.9, abs=0.01)
        assert captured_options["top_p"] == pytest.approx(0.95, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_endocrine_uses_defaults(self, action_service):
        """Without cortisol/dopamine in payload, no options_override is passed."""
        from app.cognitive.decision import ActionPlan

        plan = ActionPlan(
            action_type="RESPOND_CHAT",
            goal="ENGAGE",
            payload={
                "message": "test",
                "identity_prompt": "You are a test agent.",
                "emotion_state": "neutral",
            },
        )

        captured_options = {"_sentinel": True}

        async def capturing_stream(
            prompt, system=None, model=None, options_override=None, role=None
        ):
            if options_override is not None:
                captured_options.update(options_override)
            else:
                captured_options["_none"] = True
            yield "Default response."

        action_service.llm.generate_stream = MagicMock(side_effect=capturing_stream)

        async for _ in action_service.execute(plan):
            pass

        # Should NOT have temperature/top_p overrides
        assert "_none" in captured_options


class TestEndocrineOllamaIntegration:
    """Verify OllamaClient correctly merges options_override into payload."""

    def test_options_override_merges_with_defaults(self):
        from app.config import Config
        from app.llm.ollama_client import OllamaClient

        client = OllamaClient()
        attempts = client._build_payload_attempts(
            prompt="test",
            system=None,
            model="llama3.2:1b",
            stream=True,
            num_predict=40,
            options_override={"temperature": 0.3, "top_p": 0.7},
        )

        # Check the first attempt's options
        _, payload, _ = attempts[0]
        assert payload["options"]["temperature"] == 0.3
        assert payload["options"]["top_p"] == 0.7
        # Default values should still be present
        assert payload["options"]["num_thread"] == 6
        # Bucket 6.1: num_ctx now comes from Config.LLM_NUM_CTX, not a
        # hardcoded 2048 -- assert against the config value itself so this
        # test doesn't silently rot if the default is retuned again later.
        assert payload["options"]["num_ctx"] == Config.LLM_NUM_CTX

    def test_no_override_preserves_defaults(self):
        from app.llm.ollama_client import OllamaClient

        client = OllamaClient()
        attempts = client._build_payload_attempts(
            prompt="test",
            system=None,
            model="llama3.2:1b",
            stream=True,
            num_predict=40,
        )

        _, payload, _ = attempts[0]
        assert payload["options"]["temperature"] == 0.7
        assert payload["options"]["top_p"] == 0.9
