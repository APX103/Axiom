"""ask_user 工具。

对应原版: 0808.js:315 ask_user + 0871.js:1714 触发 awaiting_user_response。
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

    对应原版 ask_user (0808.js:315): 卡片式提问,带 options/header。
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
    "description": "Ask the user a question. Use when you need clarification or a decision. Pauses execution.",
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
