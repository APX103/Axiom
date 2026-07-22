"""Agent 状态机 — 核心循环。


这是整个系统的"大脑",驱动一轮 agent 执行。

主循环结构 (对照 0871.js:1030 _runLoop):
    while iter < max_iterations:
        1. 哨兵: cancel / biosecurity_refusal(接入点,no-op)
        2. 构建 system prompt (floor+stable+dynamic)
        3. 调 LLM (带工具)
        4. _process_llm_response 分支:
           - pause_turn / max_tokens / refusal
           - 无 tool_use → _handle_natural_completion (退出门链)
           - 有 tool_use → 执行工具 → 下一轮
        5. 工具后钩子: reviewer checkpoint(接入点,no-op)

plan mode 门控 (对照 0871.js:1625 _gatePlanProduceDenial):
    若 plan_mode 开启且无 plan → 拒绝 end_turn,最多拒绝 3 次。

接入点 (本阶段 no-op,后续阶段实现):
    - _maybe_compact: Rolling Compact (阶段 2)
    - _maybe_checkpoint: reviewer (阶段 3)
    - _check_biosecurity: biosecurity 拒绝 (后置)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from operon.frames.model import Frame
from operon.frames.service import FrameService
from operon.llm.base import LLMClient
from operon.llm.messages import (
    LLMResponse,
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from operon.llm.messages import (
    StopReason as LLMStopReason,
)
from operon.prompts.registry import build_system_prompt
from operon.tools.context import ToolContext
from operon.tools.router import ToolRouter

from .states import TERMINAL, FrameStatus, RunResultKind

logger = logging.getLogger(__name__)

# plan mode 门控: 最多拒绝次数 (对照 0871.js:1632 _planModeDenials,原版 3 次)
MAX_PLAN_DENIALS = 3


@dataclass
class RunResult:
    """agent run 的最终结果。"""

    kind: RunResultKind
    frame: Frame
    iterations: int
    final_text: str = ""
    awaiting: str | None = None  # "plan_approval" | "user_response" | None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.kind in (RunResultKind.NATURAL, RunResultKind.AWAITING)


class Agent:
    """Agent 编排器。

    一个 Agent 实例 = 一次会话/一个 frame 的执行。
    MAIN/REVIEWER/BOOKMARKER 都是 Agent,靠 agent_name 分化 (本阶段只有 MAIN)。
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        tool_router: ToolRouter,
        frame_service: FrameService,
        frame: Frame,
        ctx: ToolContext,
        max_iterations: int = 40,
        model: str | None = None,
        max_tokens: int = 8192,
        plan_mode: bool = False,
        deep_review: bool = False,
        callbacks: AgentCallbacks | None = None,
        trace_recorder: Any | None = None,
    ):
        self.llm = llm
        self.tool_router = tool_router
        self.frame_service = frame_service
        self.frame = frame
        self.ctx = ctx
        self.max_iterations = max_iterations
        self.model = model
        self.max_tokens = max_tokens
        self.plan_mode = plan_mode
        self.deep_review = deep_review
        self.callbacks = callbacks or AgentCallbacks()
        # trace 记录器 (None 或 _NullRecorder 时零开销)
        # 由 Session 在创建 Agent 时从 settings.trace 注入。
        self._trace = trace_recorder

        # 运行时状态
        self._iter = 0
        self._plan_denials = 0
        self._empty_turn_retries = 0  # end_turn 空内容重试 (对照 0871.js:1344)
        # 本轮是否已流式推过 text (避免 _process_llm_response 重复整段推)
        self._text_streamed_this_turn = False
        self._max_tokens_consecutive = 0  # max_tokens 连续次数 (对照 0858.js:2421)
        self._MAX_TOKENS_RETRY_CAP = 5  # 连续 max_tokens 上限, 超过则结束本轮
        # 检索熔断: 连续 N 轮只检索不写作 → 注入"停止检索开始写作"提示
        self._search_rounds = 0  # 连续检索轮数
        self._SEARCH_ROUND_CAP = 6  # 阈值: 超过则强制写作
        # deep_review 产出门控: 未写 .tex 就想结束 → 拒绝 (笨模型易提前 end_turn)
        self._no_output_denials = 0
        self._NO_OUTPUT_DENIAL_CAP = 3

        # Rolling Compact 状态 (对照 0858.js this._rcState)
        from operon.compact.state import new_rolling_compact_state

        self._rc_state = new_rolling_compact_state()
        self._rc_applied: set[str] = set()  # 已生效的 summary uuid
        self._rc_enabled = True  # 可由配置关闭

        # Verifier (验证 harness, 对照 0850.js oL_)
        self._verifier = None  # 按需初始化 (Reviewer 模式时)

    async def run(self, user_input: str) -> RunResult:
        """执行一次 agent 会话。"""
        # 用户在已结束 (completed/failed/cancelled) 的会话里继续发消息:
        # 重新打开 frame 进入新一轮 (对话历史保留), 而不是报错让用户新建会话。
        if self.frame.status in TERMINAL:
            self.frame_service.reopen(self.frame.id)
        # 初始化: 用户消息入历史
        self.frame.messages.append(Message(role=Role.USER, content=user_input))
        self.frame.task_summary = user_input[:200]
        # 恢复 ask_user: 用户带着回答回来, 清掉 AWAITING_USER_RESPONSE 回到 PROCESSING,
        # 否则 _run_loop 顶部的哨兵会立即 return, 回答永远不被处理。
        if self.frame.status == FrameStatus.AWAITING_USER_RESPONSE:
            self.frame.status = FrameStatus.PROCESSING
            # 清掉挂起的问题, 避免后续 complete 事件重复上报
            if getattr(self.ctx, "pending_ask", None) is not None:
                self.ctx.pending_ask = None
        await self.callbacks.on_start(self.frame)
        if self.ctx.plan.steps:
            await self.callbacks.on_plan_update(self.ctx.plan)

        # 记忆召回: 用用户消息做 BM25 搜索, 注入 [Memory] 块
        await self._recall_memory(user_input)

        try:
            result = await self._run_loop()
            # 记忆提取: 真异步 (不阻塞 complete 事件的发送)
            asyncio.create_task(self._extract_memories_background())
            return result
        except asyncio.CancelledError:
            self.frame_service.update_status(self.frame.id, FrameStatus.CANCELLED)
            return self._result(RunResultKind.CANCELLED)
        except Exception as e:
            self.frame_service.update_status(self.frame.id, FrameStatus.FAILED)
            return self._result(RunResultKind.ERROR, error=f"{type(e).__name__}: {e}")

    async def _call_llm(
        self,
        messages: list,
        *,
        system: str | None,
        tools: list | None,
    ) -> LLMResponse:
        """调 LLM, 优先流式 (text delta 增量回调), 不支持则回退非流式。

        流式路径: 遍历 chat_stream 的 text delta, 每个 delta 调 on_assistant_text
        (前端打字机效果), 流结束拿到完整 LLMResponse。置 _text_streamed_this_turn=True
        让 _process_llm_response 跳过重复的整段 text 回调。
        非流式路径: 直接 chat(), 由 _process_llm_response 统一回调整段 text。
        """
        client = self.llm
        # 判断是否支持流式: 子类覆盖了 base 的 NotImplementedError 默认实现
        from operon.llm.base import LLMClient

        can_stream = (
            hasattr(client, "chat_stream")
            and LLMClient.chat_stream is not type(client).chat_stream
        )

        if can_stream:
            try:
                final_resp: LLMResponse | None = None
                async for ev in client.chat_stream(
                    messages,
                    system=system,
                    tools=tools,
                    model=self.model,
                    max_tokens=self.max_tokens,
                ):
                    if ev.get("type") == "reasoning" and ev.get("delta"):
                        await self.callbacks.on_assistant_thinking(ev["delta"])
                    elif ev.get("type") == "text" and ev.get("delta"):
                        await self.callbacks.on_assistant_text(ev["delta"])
                        self._text_streamed_this_turn = True
                    elif ev.get("type") == "final":
                        final_resp = ev["response"]
                if final_resp is not None:
                    return final_resp
                # 流式无输出 (异常情况), 回退非流式
            except NotImplementedError:
                pass
            except Exception as e:
                # 检测 prompt-too-long 错误 → 反馈到 Rolling Compact
                if self._is_prompt_too_long_error(e):
                    self._handle_overflow()
                # 流式失败 (网络/兼容性) 不致命, 回退非流式重试一次
                pass

        # 非流式回退
        try:
            return await client.chat(
                messages,
                system=system,
                tools=tools,
                model=self.model,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            if self._is_prompt_too_long_error(e):
                self._handle_overflow()
            raise

    def _is_prompt_too_long_error(self, e: Exception) -> bool:
        """检测是否为 prompt-too-long / context_length_exceeded 错误。"""
        msg = str(e).lower()
        return any(kw in msg for kw in [
            "context_length_exceeded", "prompt too long", "maximum context",
            "context window", "token limit exceeded", "too many tokens",
        ])

    def _handle_overflow(self) -> None:
        """prompt-too-long 时反馈到 Rolling Compact 压力状态。"""
        from operon.compact.engine import note_overflow

        can_retry = note_overflow(self._rc_state, overflow_tokens=0)
        if can_retry:
            logger.warning("prompt too long; RC overflow noted, will attempt compaction next turn")
        else:
            logger.error("prompt too long; RC retry cap exhausted")

    async def _recall_memory(self, user_input: str) -> None:
        """记忆召回: 用用户消息做 BM25 搜索, 注入 [Memory] 块到上下文。"""
        if self.ctx.memory_store is None or self.ctx.memory_index is None:
            return
        try:
            from operon.config import load_settings
            from operon.memory.recall import recall, render_recall_block

            # recall_inject_max 从 config 读 (修死配置: 原来硬编码 limit=6)
            try:
                settings = load_settings()
                recall_limit = settings.memory.recall_inject_max
            except Exception:
                recall_limit = 6

            results = recall(
                user_input, self.ctx.memory_index,
                limit=recall_limit, exclude_entities=["frame"],
                project_id=self.ctx.project_id,
            )
            if results:
                # 标记 surfaced
                await self.ctx.memory_store.mark_surfaced([r["id"] for r in results])
                # 注入为 harness-notice (用户不可见, agent 可见)
                block = render_recall_block(results)
                self.frame.messages.append(Message(
                    role=Role.USER, content=block, _harness_notice=True,
                ))
        except Exception as e:
            logger.warning("memory recall failed: %s", e)

    async def _extract_memories_background(self) -> None:
        """记忆提取: 从本轮对话提取持久事实, 异步写入。"""
        if self.ctx.memory_store is None:
            return
        # 没有 DB 时不做提取 (测试/CLI 场景)
        if getattr(self.ctx.memory_store, "db_factory", None) is None:
            return
        try:
            from operon.config import load_settings
            from operon.memory.extract import apply_extraction, extract_memories

            settings = load_settings()
            if not settings.memory.extract_enabled:
                return

            # 取本轮所有消息
            messages = self.frame.messages
            if not messages:
                return

            existing = await self.ctx.memory_store.list_all()
            ops = await extract_memories(
                messages, existing, self.llm,
                frame_id=self.frame.id,
                max_per_run=settings.memory.extract_max_per_run,
            )
            # Layer A: session_id 透传 (优先 ctx.session_id, fallback frame.id)
            session_id = self.ctx.session_id or self.frame.id
            # Layer A.5: project_id 透传 (从 ctx.project_id 拿)
            project_id = getattr(self.ctx, "project_id", None)
            count = await apply_extraction(
                self.ctx.memory_store, ops,
                frame_id=self.frame.id, session_id=session_id,
                project_id=project_id,
            )
            if count > 0:
                logger.info("memory extraction: %d operations applied", count)
                # 重建索引
                from operon.memory.recall import build_index

                all_mems = await self.ctx.memory_store.list_all()
                self.ctx.memory_index = build_index(all_mems)
        except Exception as e:
            logger.warning("memory extraction failed: %s", e)

    async def _run_loop(self) -> RunResult:
        """主循环。"""
        while self._iter < self.max_iterations:
            # 1. 哨兵检查
            if self.frame.status == FrameStatus.CANCELLED:
                return self._result(RunResultKind.CANCELLED)
            if self.frame.status in TERMINAL:
                return self._result(
                    RunResultKind.ERROR,
                    error=f"frame became terminal ({self.frame.status.value}) during run",
                )
            if self.frame.status == FrameStatus.AWAITING_PLAN_APPROVAL:
                # deep_review 模式: 自动批准 plan, 不阻断 (无人值守深度流程不该卡审批)
                if self.deep_review:
                    from operon.tools.builtins import plan as plan_tools
                    await plan_tools.approve_plan(self.ctx)
                    await self.callbacks.on_plan_update(self.ctx.plan)
                    logger.info("deep_review: auto-approved plan, continuing")
                else:
                    return self._result(RunResultKind.AWAITING, awaiting="plan_approval")
            if self.frame.status == FrameStatus.AWAITING_USER_RESPONSE:
                # ask_user → 退出循环,等待用户
                return self._result(RunResultKind.AWAITING, awaiting="user_response")

            # 2. 接入点: biosecurity (no-op,后置)
            # if await self._check_biosecurity(): return self._result(ERROR, ...)

            # 3. 接入点: rolling compact (no-op,阶段 2)
            # await self._maybe_compact()

            self._iter += 1
            await self.callbacks.on_iteration(self._iter)
            # trace: 标记当前轮次上下文 (供 span 共享 frame_id/turn_id)
            turn_id = f"{self.frame.id}-{self._iter}"
            if self._trace is not None:
                self._trace.set_context(frame_id=self.frame.id, turn_id=turn_id)

            # 3. Rolling Compact: 每轮调 LLM 前压缩 (对照 0871.js:1106 checkRcTurn)
            await self._maybe_compact()

            # 4. 构建 system prompt + 调 LLM (用投影后的消息)
            system = build_system_prompt(
                self.ctx, plan_mode=self.plan_mode, deep_review=self.deep_review
            )
            tools = self.tool_router.registry.definitions()
            llm_messages = self._prepare_messages_for_llm()
            # 优先走流式 (LLM 支持时, 每个 text delta 增量回调 → 前端打字机效果)
            self._text_streamed_this_turn = False
            # trace: 包一层 LLM span (记录 model / token 用量 / stop_reason / 耗时)
            if self._trace is not None:
                with self._trace.span("llm_call", kind="llm", model=self.model or "unknown"):
                    resp = await self._call_llm(
                        llm_messages, system=system, tools=tools or None
                    )
                    # 在 span 内补 attrs (退出前 set 才会落盘)
                    if resp is not None:
                        self._trace.set_attrs(
                            input_tokens=(
                                getattr(resp.usage, "input_tokens", None) if resp.usage else None
                            ),
                            output_tokens=(
                                getattr(resp.usage, "output_tokens", None) if resp.usage else None
                            ),
                            stop_reason=resp.stop_reason.value if resp.stop_reason else None,
                            iter=self._iter,
                        )
            else:
                resp = await self._call_llm(
                    llm_messages, system=system, tools=tools or None
                )
            # 记录 server-side token (用于 RC 锚点估算)
            self.frame.add_usage(resp.usage)

            # 5. 处理响应
            should_exit, result = await self._process_llm_response(resp)
            if should_exit:
                return result  # type: ignore[return-value]

        # 达到最大迭代数
        self.frame_service.update_status(self.frame.id, FrameStatus.COMPLETED)
        return self._result(RunResultKind.MAX_ITERS)

    async def _process_llm_response(self, resp: LLMResponse) -> tuple[bool, RunResult | None]:
        """处理单次 LLM 响应。

        返回 (should_exit, exit_result)。should_exit=True 时 exit_result 有效。
        """
        # refusal → 错误退出
        if resp.stop_reason == LLMStopReason.REFUSAL:
            self.frame.messages.append(Message(role=Role.ASSISTANT, content=resp.content))
            self.frame_service.update_status(self.frame.id, FrameStatus.FAILED)
            return True, self._result(RunResultKind.ERROR, error="model refused")

        # 提取 tool_use blocks
        tool_uses = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        text_blocks = [b for b in resp.content if isinstance(b, TextBlock)]
        thinking_blocks = [b for b in resp.content if isinstance(b, ThinkingBlock)]

        # 把 assistant 输出加入历史 (即使有 tool_use,文本也保留)
        if resp.content:
            # server-anchor: 把真实 token 用量写到消息上, 供 RC 精确估算
            assistant_msg = Message(role=Role.ASSISTANT, content=resp.content)
            if resp.usage and resp.usage.input_tokens:
                assistant_msg.server_input_tokens = resp.usage.input_tokens
                assistant_msg.server_output_tokens = resp.usage.output_tokens
            self.frame.messages.append(assistant_msg)
            # 非流式路径: 推送 thinking (流式路径已在 _call_llm 里增量推过)
            if thinking_blocks and not getattr(self, "_text_streamed_this_turn", False):
                await self.callbacks.on_assistant_thinking(
                    "".join(b.thinking for b in thinking_blocks)
                )
            # 非流式路径: 推送 text
            if text_blocks and not getattr(self, "_text_streamed_this_turn", False):
                await self.callbacks.on_assistant_text("".join(b.text for b in text_blocks))

        # max_tokens: 响应被截断 — 用 harness-notice 模式, 不泄漏到前端
        # 对照原版 handleMaxTokens (0858.js:2417): 注入 _harness_notice 消息, 前端不可见
        if resp.stop_reason == LLMStopReason.MAX_TOKENS:
            self._max_tokens_consecutive += 1
            if tool_uses:
                # 有 tool_use 但被截断: 丢弃可能不完整的 tool_use, 继续执行完整的
                # (简化: 保留所有 tool_use, 让工具执行)
                pass
            else:
                # 无 tool_use: 注入 harness-notice 让循环继续 (用户不可见)
                if self._max_tokens_consecutive >= self._MAX_TOKENS_RETRY_CAP:
                    # 超过上限: 结束本轮
                    self.frame.messages.append(Message(
                        role=Role.USER,
                        content=(
                            "(Output token limit hit repeatedly. Ending this turn — "
                            "please rephrase or break into smaller steps.)"
                        ),
                        _harness_notice=True,
                    ))
                    return True, self._result(RunResultKind.COMPLETED)
                elif self._max_tokens_consecutive >= 3:
                    self.frame.messages.append(Message(
                        role=Role.USER,
                        content=(
                            "(Your response hit the output token limit repeatedly. "
                            "Drastically reduce the scope of your next response and "
                            "produce a brief summary instead.)"
                        ),
                        _harness_notice=True,
                    ))
                else:
                    self.frame.messages.append(Message(
                        role=Role.USER,
                        content=(
                            "(Your previous response was truncated by the output token limit. "
                            "Break your work into smaller chunks and continue.)"
                        ),
                        _harness_notice=True,
                    ))
                return False, None
        else:
            # 非 max_tokens: 重置计数
            self._max_tokens_consecutive = 0

        # 无 tool_use 且非 max_tokens → 自然完成路径 (退出门链)
        if not tool_uses:
            return await self._handle_natural_completion(resp)

        # 有 tool_use → 执行工具
        await self.callbacks.on_tool_calls(tool_uses)
        # trace: 每个工具调用一个 span (含耗时和错误)
        if self._trace is not None and self._trace.log_tool_args:
            # 记录工具调用元信息 (不展开参数, 敏感)
            for tu in tool_uses:
                self._trace.event(
                    "tool_call",
                    tool=tu.name,
                    iter=self._iter,
                )
        results = await self.tool_router.execute_tool_calls(tool_uses)
        # trace: 工具结果摘要 (可选, 默认开)
        if self._trace is not None and self._trace.log_tool_result_summary:
            for r in results:
                self._trace.event(
                    "tool_result",
                    tool=getattr(r, "tool_use_id", None),
                    is_error=getattr(r, "is_error", False),
                    summary=self._trace.summarize_result(r.content),
                )
        await self.callbacks.on_tool_results(results)
        if any(tu.name in {"generate_plan", "update_step_status"} for tu in tool_uses):
            await self.callbacks.on_plan_update(self.ctx.plan)

        # 工具结果加入历史 (Anthropic 风格: tool_result 作为 user 消息的 content block)
        self.frame.messages.append(Message(role=Role.USER, content=results))

        # 检索熔断: 统计本轮工具类型, 连续纯检索超阈值则强制写作
        called_names = {tu.name for tu in tool_uses}
        writing_tools = {"write_file", "edit_file", "save_artifacts"}
        search_tools = {"search_papers", "web_search", "fetch_paper", "fetch_url", "search_skills"}
        if called_names & writing_tools:
            self._search_rounds = 0  # 写了文件, 重置
        elif called_names & search_tools:
            self._search_rounds += 1
        if self._search_rounds >= self._SEARCH_ROUND_CAP and not (called_names & writing_tools):
            n = self._search_rounds
            self._search_rounds = 0  # 重置避免反复注入
            self.frame.messages.append(Message(
                role=Role.USER,
                content=(
                    f"You have done {n} consecutive rounds of searching. "
                    "You have enough literature. STOP searching and START writing the survey now: "
                    "call write_file to create main.tex and references.bib "
                    "with the papers you already found. "
                    "Do not search again."
                ),
                _harness_notice=True,
            ))

        # submit_output (子 agent 结构化返回): 标记被设则当作完成退出
        if self.frame.context.get("_submit_output_called"):
            submitted = self.frame.context.get("_submitted_output", {})
            bullets = submitted.get("completion_bullets", [])
            text = "Subtask complete."
            if bullets:
                text += " " + " ".join(bullets)
            return True, self._result(RunResultKind.NATURAL, final_text=text)

        # 工具可能触发了等待状态
        # (generate_plan → awaiting_plan_approval, ask_user → awaiting_user_response)
        if self.frame.status in (
            FrameStatus.AWAITING_PLAN_APPROVAL,
            FrameStatus.AWAITING_USER_RESPONSE,
        ):
            awaiting = (
                "plan_approval"
                if self.frame.status == FrameStatus.AWAITING_PLAN_APPROVAL
                else "user_response"
            )
            return True, self._result(RunResultKind.AWAITING, awaiting=awaiting)

        # Reviewer checkpoint (阈值触发, 对照 0871.js:1463)
        await self._maybe_checkpoint()

        return False, None

    def _has_written_tex(self) -> bool:
        """检查工作区是否已写入 .tex 文件 (deep_review 产出门控用)。"""
        try:
            ws = self.ctx.workspace
            if ws is None:
                return False
            return any(p.suffix == ".tex" for p in ws.rglob("*.tex"))
        except Exception:
            return False

    async def _handle_natural_completion(self, resp: LLMResponse) -> tuple[bool, RunResult | None]:
        """自然完成路径 (无工具调用时的退出门链)。

        本阶段实现 plan_produce_denial 门 (对照 1625)。
        """
        # 空内容 end_turn 重试 (对照 0871.js:1344) — harness-notice, 用户不可见
        if not resp.content and self._empty_turn_retries < 3:
            self._empty_turn_retries += 1
            # deep_review: 空响应时给出强任务提醒 (笨模型容易加载 skill 后空转)
            if self.deep_review:
                hint = (
                    "You returned an empty response but the survey is not written yet. "
                    "Continue now: search for papers with search_papers (3-5 rounds), "
                    "then write BOTH main.tex and references.bib. Do not end without "
                    "producing these files."
                )
            else:
                hint = "(Please continue your work or provide a response.)"
            self.frame.messages.append(Message(
                role=Role.USER, content=hint, _harness_notice=True,
            ))
            return False, None

        # plan mode 门控: 未生成 plan 则拒绝结束 (对照 0871.js:1625 _gatePlanProduceDenial)
        if self.plan_mode and not self.ctx.plan.steps:
            if self._plan_denials < MAX_PLAN_DENIALS:
                self._plan_denials += 1
                self.frame.messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            "Plan mode is active. You MUST call `generate_plan` first "
                            "with your planned steps "
                            "before you can finish. "
                            f"(denial {self._plan_denials}/{MAX_PLAN_DENIALS})"
                        ),
                        # 内部门控提示, 不渲染给用户
                        _harness_notice=True,
                    )
                )
                await self.callbacks.on_event("plan_denial", "plan required before completion")
                return False, None

        # deep_review 产出门控: 未写 .tex 文件就想结束 → 拒绝, 强制继续写作
        # (笨模型倾向: 加载 skill + 检索几轮后提前 end_turn, 却从未写文件)
        if self.deep_review and self._no_output_denials < self._NO_OUTPUT_DENIAL_CAP:
            if not self._has_written_tex():
                self._no_output_denials += 1
                self.frame.messages.append(Message(
                    role=Role.USER,
                    content=(
                        "You are trying to finish, but you have NOT written any output file yet. "
                        "The survey is incomplete. STOP ending your turn and WRITE the files now: "
                        "call write_file to create references.bib first, then main.tex, using the "
                        "papers you already found. "
                        f"(denial {self._no_output_denials}/{self._NO_OUTPUT_DENIAL_CAP})"
                    ),
                    _harness_notice=True,
                ))
                return False, None

        # Terminal barrier: 最终 reviewer 审查 (对照 0871.js:1836 _gateReviewerTerminalBarrier)
        barrier = await self._terminal_barrier()
        if barrier["veto"]:
            notice = barrier["notice"]
            self.frame.messages.append(
                Message(role=Role.USER, content=notice["text"], _harness_notice=True)
            )
            await self.callbacks.on_event(
                "reviewer_findings", f"reviewer 发现 {len(notice['findings'])} 个问题,要求修复"
            )
            return False, None  # 继续循环给 agent 修复机会

        # 正常完成
        final_text = "".join(b.text for b in resp.content if isinstance(b, TextBlock))
        if self.plan_mode and self.ctx.plan.approved and self.ctx.plan.steps:
            from operon.tools.builtins import plan as plan_tools

            if plan_tools.complete_unfinished_steps(self.ctx):
                await self.callbacks.on_plan_update(self.ctx.plan)
        self.frame_service.update_status(self.frame.id, FrameStatus.COMPLETED)
        await self.callbacks.on_complete(final_text)
        return True, self._result(RunResultKind.NATURAL, final_text=final_text)

    def _result(
        self,
        kind: RunResultKind,
        *,
        final_text: str = "",
        awaiting: str | None = None,
        error: str | None = None,
    ) -> RunResult:
        return RunResult(
            kind=kind,
            frame=self.frame,
            iterations=self._iter,
            final_text=final_text,
            awaiting=awaiting,
            error=error,
            usage={
                "input_tokens": self.frame.input_tokens,
                "output_tokens": self.frame.output_tokens,
            },
        )

    async def _maybe_compact(self) -> None:
        """Rolling Compact 检查。对照 0871.js:1106 checkRcTurn。

        每轮调 LLM 前调用。若上下文接近 budget 则触发压缩。
        """
        if not self._rc_enabled:
            return
        from operon.compact.engine import check_rolling_compact
        from operon.config import RollingCompactConfig

        cfg: RollingCompactConfig = (
            getattr(self.ctx, "rolling_compact_config", None) or RollingCompactConfig()
        )
        if not cfg.enabled:
            return
        # context_window 优先用模型实际值 (ctx.context_window),否则用 config 默认
        # 重要: 必须匹配真实模型上下文 (256K 模型不能配 500K,否则 Compact 会在爆窗后才触发)
        ctx_cw = getattr(self.ctx, "context_window", None)
        context_window = ctx_cw if ctx_cw else cfg.context_ceiling

        result = await check_rolling_compact(
            self.frame.messages,
            self._rc_state,
            self.llm,
            context_window=context_window,
            ka_ratio=cfg.ka_ratio,
            frame_id=self.frame.id,
            applied_summary_uuids=self._rc_applied,
            model=self.model,
        )
        if result.type == "applied":
            await self.callbacks.on_event(
                "rolling_compact",
                f"L1/L2 折叠: {getattr(result, 'count', 0)} 条, 释放 "
                f"{getattr(result, 'tokens_freed', 0)} tokens",
            )

    def _prepare_messages_for_llm(self) -> list[Message]:
        """投影后的消息列表 (RC 生效时)。对照 0858.js:2514 prepareMessagesForLlm。"""
        if not self._rc_enabled or not self._rc_applied:
            return self.frame.messages
        from operon.compact.projection import prepare_messages_for_llm

        return prepare_messages_for_llm(self.frame.messages, self._rc_applied)

    def _init_verifier(self) -> None:
        """初始化 Verifier (验证 harness)。对照原版 _initCollaborators (0871.js:968)。"""
        if self._verifier is not None:
            return
        from operon.config import VerificationConfig as SettingsVerificationConfig
        from operon.config import load_settings
        from operon.verify.verifier import VerificationConfig as VerifierConfig
        from operon.verify.verifier import Verifier

        settings_cfg = (
            getattr(self.ctx, "verification_config", None) or SettingsVerificationConfig()
        )
        # 收敛自动触发: plan 钉了 research_question 时, 即使 cfg.enabled=False 也开启 verifier,
        # 让 reviewer 检查后半段是否跑偏。普通问答/代码任务 (无 research_question) 不受影响。
        has_anchor = bool(getattr(self.ctx.plan, "research_question", None))
        if not settings_cfg.enabled and not has_anchor:
            return
        if not settings_cfg.enabled and has_anchor:
            # 复制一份并开启, 不改原 cfg (避免污染共享配置)。
            settings_cfg = settings_cfg.model_copy(update={"enabled": True})

        # Reviewer 模型: 优先 [verification].reviewer_model, 其次 [models].reviewer.model,
        # 最后回退到会话主模型。
        settings = load_settings()
        reviewer_model = settings_cfg.reviewer_model
        if not reviewer_model and settings.models and settings.models.reviewer:
            reviewer_model = settings.models.reviewer.model
        if not reviewer_model:
            reviewer_model = self.model

        # operon.config.VerificationConfig (Pydantic settings) 与
        # operon.verify.verifier.VerificationConfig (dataclass) 字段不同, 不能直接混用。
        # 这里从 settings 取出通用字段, 构造 verifier 专用配置。
        verifier_cfg = VerifierConfig(
            enabled=settings_cfg.enabled,
            reviewer_model=reviewer_model,
            reviewer_max_iterations=settings_cfg.reviewer_max_iterations,
            min_checkpoint_interval_ms=settings_cfg.min_checkpoint_interval_ms,
            max_consecutive_bounces=settings_cfg.max_consecutive_bounces,
        )

        self._verifier = Verifier(
            llm=self.llm,
            frame_service=self.frame_service,
            frame=self.frame,
            config=verifier_cfg,
            reviewer_model=reviewer_model,
            plan=self.ctx.plan,
        )

    async def _maybe_checkpoint(self) -> None:
        """Reviewer checkpoint 触发检查。对照原版 0871.js:1463。"""
        self._init_verifier()
        if self._verifier is None:
            return
        if self._verifier.maybe_checkpoint():
            findings = await self._verifier.checkpoint()
            if findings:
                fail_warn = [f for f in findings if f.is_actionable]
                if fail_warn:
                    await self.callbacks.on_event(
                        "reviewer_checkpoint",
                        f"checkpoint 审查: {len(fail_warn)} 个 fail/warn finding",
                    )

    async def _terminal_barrier(self) -> dict:
        """Terminal barrier: 自然完成时的最终审查。对照 0871.js:1836。"""
        self._init_verifier()
        if self._verifier is None:
            return {"veto": False, "notice": None}
        return await self._verifier.terminal_barrier()


@dataclass
class AgentCallbacks:
    """agent 执行回调。

    子类化覆盖方法来接入日志/UI/遥测。默认 no-op。
    """

    async def on_start(self, frame: Frame) -> None: ...
    async def on_iteration(self, n: int) -> None: ...
    async def on_assistant_thinking(self, text: str) -> None: ...
    async def on_assistant_text(self, text: str) -> None: ...
    async def on_tool_calls(self, tool_uses: list[ToolUseBlock]) -> None: ...
    async def on_tool_results(self, results: list[ToolResultBlock]) -> None: ...
    async def on_plan_update(self, plan: Any) -> None: ...
    async def on_event(self, event: str, detail: str) -> None: ...
    async def on_complete(self, final_text: str) -> None: ...
