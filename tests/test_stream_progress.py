"""Stream progress handler tests."""

from unittest.mock import AsyncMock

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter
from telegram_cursor_agent.agent.stream_progress import StreamProgressHandler


async def test_emits_assistant_step_before_tool_call(test_settings, runner) -> None:
    on_progress = AsyncMock()
    handler = StreamProgressHandler(on_progress, CursorAgentAdapter._event_text)
    event = {
        "type": "assistant",
        "model_call_id": "call-1",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Проверю живой сайт."}],
        },
    }
    await handler.handle(event)
    on_progress.assert_awaited_once_with("Проверю живой сайт.")
    assert handler.last_assistant == "Проверю живой сайт."


async def test_skips_partial_assistant_deltas(test_settings, runner) -> None:
    on_progress = AsyncMock()
    handler = StreamProgressHandler(on_progress, CursorAgentAdapter._event_text)
    event = {
        "type": "assistant",
        "timestamp_ms": 123,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Пров"}],
        },
    }
    await handler.handle(event)
    on_progress.assert_not_awaited()


async def test_tracks_last_assistant_for_final_reply(test_settings, runner) -> None:
    on_progress = AsyncMock()
    handler = StreamProgressHandler(on_progress, CursorAgentAdapter._event_text)
    await handler.handle(
        {
            "type": "assistant",
            "model_call_id": "call-1",
            "message": {"content": [{"type": "text", "text": "Шаг 1"}]},
        }
    )
    await handler.handle(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Финальный ответ"}]},
        }
    )
    assert handler.last_assistant == "Финальный ответ"
    assert on_progress.await_count == 1
