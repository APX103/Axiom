"""ask_user 工具。


agent 需要用户输入时调用,frame 进入 awaiting_user_response。
"""

from __future__ import annotations

from operon.agent.states import FrameStatus
from operon.tools.context import PendingUserAsk, ToolContext


async def ask_user(
    ctx: ToolContext,
    question: str,
    options: list[str] | None = None,
    header: str | None = None,
) -> str:
    """向用户提问。


    触发 frame → awaiting_user_response。
    """
    ctx.pending_ask = PendingUserAsk(question=question, options=options or [])
    ctx.frame_service.update_status(ctx.frame.id, FrameStatus.AWAITING_USER_RESPONSE)
    prefix = f"[{header}] " if header else ""
    opt_text = ""
    if options:
        opt_text = "\nOptions: " + ", ".join(options)
    return f"{prefix}{question}{opt_text}\n(Awaiting user response)"


ASK_USER_SPEC = {
    "name": "ask_user",
    "description": (
        "Ask the user a question. Use when you need clarification or a "
        "decision. Pauses execution."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional multiple-choice options",
            },
            "header": {"type": "string", "description": "Optional short header"},
        },
        "required": ["question"],
    },
}
