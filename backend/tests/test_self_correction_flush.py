from unittest.mock import AsyncMock

import pytest

from app.cognitive.action import ActionService


@pytest.mark.asyncio
async def test_self_correction_publishes_scoped_flush_without_turn_cancellation():
    service = ActionService()
    service.publish_cb = AsyncMock()

    await service._announce_self_correction("bad first take", "turn-42")

    service.publish_cb.assert_awaited_once_with(
        "audio.stop",
        {
            "interrupt": True,
            "flush": True,
            "reason": "bad first take",
            "turn_id": "turn-42",
        },
    )
