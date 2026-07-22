"""WebSocket 事件协议的契约测试。"""

from operon.api.events import CompleteEvent, StartEvent


def test_start_event_matches_callback_payload() -> None:
    event = StartEvent(frame_id="frame-1", task_summary="test")

    assert event.model_dump() == {
        "type": "start",
        "frame_id": "frame-1",
        "task_summary": "test",
    }


def test_complete_event_supports_pending_ask_and_isolated_defaults() -> None:
    first = CompleteEvent(
        kind="awaiting",
        pending_ask={"question": "继续吗？", "options": ["继续", "停止"]},
    )
    second = CompleteEvent(kind="natural")

    first.usage["input_tokens"] = 1
    first.artifacts["main.tex"] = {"size": 10}

    assert first.pending_ask == {
        "question": "继续吗？",
        "options": ["继续", "停止"],
    }
    assert second.usage == {}
    assert second.artifacts == {}
