"""CLI 入口。

对应原版: 2558.js CLI 入口 + 2547.js serveMain。
本阶段: version / info / chat (单轮) / run (完整 agent 多轮)。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from operon import __version__
from operon.config import load_settings

app = typer.Typer(
    name="operon",
    help="operon-py: Operon 的 Python clean-room 复刻",
    no_args_is_help=True,
)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="监听地址 (0.0.0.0=局域网可访问)"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="热重载 (开发)"),
) -> None:
    """启动 HTTP/WebSocket API 服务。

    前端对接此服务。默认 0.0.0.0 允许局域网访问。
    """
    import uvicorn

    typer.secho(f"operon-py API → http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    typer.secho(f"  文档: http://{host}:{port}/docs", fg=typer.colors.CYAN)
    typer.secho(f"  WS:   ws://{host}:{port}/api/sessions/{{sid}}/stream", fg=typer.colors.CYAN)
    uvicorn.run("operon.api.app:app", host=host, port=port, reload=reload)


@app.command()
def version() -> None:
    """打印版本。"""
    typer.echo(f"operon-py {__version__}")


@app.command()
def info() -> None:
    """打印配置/环境信息 (用于诊断)。"""
    settings = load_settings()
    typer.echo(f"version:    {__version__}")
    typer.echo(f"data_dir:   {settings.data_dir_resolved()}")
    typer.echo(f"db_url:     {settings.db_url()}")
    typer.echo(f"rc ceiling: {settings.rolling_compact.context_ceiling}")
    typer.echo(f"rc ka_ratio:{settings.rolling_compact.ka_ratio}")
    typer.echo("models:")
    if settings.models:
        for tier in ("small", "medium", "large"):
            m = getattr(settings.models, tier)
            if m is None:
                continue
            masked = m.api_key[:8] + "..." if m.api_key else "(unset)"
            typer.echo(f"  {tier:7} → {m.model} @ {m.base_url or '(default)'}  key={masked}")
    else:
        typer.echo("  (未配置 models — 通过 config.toml 或环境变量设置)")


@app.command()
def chat(
    prompt: str = typer.Argument(..., help="发送的提示词"),
    model: str = typer.Option(None, "--model", "-m", help="模型名 (默认用配置的 large)"),
    base_url: str = typer.Option(None, "--base-url", help="覆盖 API base url"),
) -> None:
    """单轮连通测试: 发一条消息,打印响应。

    用于阶段 0 验收: 验证能连上国内模型并拿到响应 (含 tool_use)。

    需要 API key (OPERON_MODELS__LARGE__API_KEY 环境变量或 config.toml)。
    """
    from operon.llm.messages import Message, Role
    from operon.llm.openai_compat import OpenAICompatClient

    settings = load_settings()
    if not settings.models:
        typer.secho(
            "错误: 未配置模型。请设置环境变量或 config.toml。\n"
            "示例:\n"
            "  export OPERON_MODELS__LARGE__MODEL=deepseek-chat\n"
            "  export OPERON_MODELS__LARGE__BASE_URL=https://api.deepseek.com/v1\n"
            "  export OPERON_MODELS__LARGE__API_KEY=sk-...",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    tier = settings.models.tier(settings.default_model_tier)
    client = OpenAICompatClient(
        base_url=base_url or tier.base_url or "https://api.openai.com/v1",
        api_key=tier.api_key or os.environ.get("OPENAI_API_KEY", ""),
        model=model or tier.model,
    )

    async def _run() -> None:
        try:
            resp = await client.chat(
                [Message(role=Role.USER, content=prompt)],
                system="You are a helpful assistant. Reply concisely.",
                max_tokens=512,
            )
            for block in resp.content:
                if hasattr(block, "text"):
                    typer.echo(block.text)
                else:
                    typer.secho(f"[tool_use] {block}", fg=typer.colors.CYAN)
            typer.secho(
                f"\n[stop={resp.stop_reason.value} "
                f"in={resp.usage.input_tokens} out={resp.usage.output_tokens}]",
                fg=typer.colors.GREEN,
            )
        finally:
            await client.close()

    asyncio.run(_run())


@app.command()
def run(
    prompt: str = typer.Argument(..., help="任务描述"),
    workspace: str = typer.Option(".", "--workspace", "-w", help="工作区目录"),
    plan_mode: bool = typer.Option(False, "--plan-mode", help="启用 plan mode (先规划后执行)"),
    max_iterations: int = typer.Option(40, "--max-iter", help="最大迭代轮数"),
    base_url: str = typer.Option(None, "--base-url", help="覆盖 API base url"),
    model: str = typer.Option(None, "--model", "-m", help="模型名"),
) -> None:
    """运行完整 agent (多轮工具调用)。

    阶段 1 验收: agent 循环 + 工具执行 + plan mode。
    示例:
      operon run "列出当前目录文件并统计数量"
      operon run "帮我规划一个数据分析任务" --plan-mode
    """
    from pathlib import Path

    from operon.agent.runner import AgentCallbacks
    from operon.agent.session import Session, SessionConfig
    from operon.llm.openai_compat import OpenAICompatClient

    settings = load_settings()
    if not settings.models:
        typer.secho("错误: 未配置模型 (设 OPERON_MODELS__LARGE__* 环境变量)", fg=typer.colors.RED)
        raise typer.Exit(1)

    tier = settings.models.tier(settings.default_model_tier)
    client = OpenAICompatClient(
        base_url=base_url or tier.base_url or "https://api.openai.com/v1",
        api_key=tier.api_key or os.environ.get("OPENAI_API_KEY", ""),
        model=model or tier.model,
    )

    class CLICallbacks(AgentCallbacks):
        async def on_iteration(self, n: int) -> None:
            typer.secho(f"\n── 轮次 {n} ──", fg=typer.colors.BLUE, bold=True)

        async def on_assistant_text(self, text: str) -> None:
            if text.strip():
                typer.echo(text)

        async def on_tool_calls(self, tool_uses: list) -> None:
            for tu in tool_uses:
                args_preview = ", ".join(f"{k}={v!r:.40}" for k, v in tu.input.items())
                typer.secho(f"  ⚙ 调用 {tu.name}({args_preview})", fg=typer.colors.CYAN)

        async def on_tool_results(self, results: list) -> None:
            for r in results:
                mark = "✗" if r.is_error else "✓"
                color = typer.colors.RED if r.is_error else typer.colors.GREEN
                preview = (r.content if isinstance(r.content, str) else str(r.content))[:200]
                typer.secho(f"  {mark} {preview}", fg=color, dim=True)

        async def on_event(self, event: str, detail: str) -> None:
            typer.secho(f"  ! [{event}] {detail}", fg=typer.colors.YELLOW)

    # MCP servers / api_keys 从 config.toml 读 (CLI 也走和 API 一样的配置)
    from operon.mcp.manager import MCPServerConfig

    mcp_servers = [
        MCPServerConfig(
            name=s["name"], url=s["url"], headers=s.get("headers", {})
        )
        for s in settings.mcp_servers
    ] if settings.mcp_servers else None
    api_keys = {k: v for k, v in settings.api_keys.items() if v} or None

    session = Session(
        llm=client,
        config=SessionConfig(
            workspace=Path(workspace).resolve(),
            plan_mode=plan_mode,
            max_iterations=max_iterations,
            model=model or tier.model,
            context_window=tier.context_window,
            mcp_servers=mcp_servers,
            api_keys=api_keys,
        ),
        callbacks=CLICallbacks(),
    )

    async def _run() -> None:
        try:
            result = await session.run(prompt)
            typer.secho("\n" + "─" * 40, fg=typer.colors.BLUE)
            kind_color = {
                "natural": typer.colors.GREEN,
                "awaiting": typer.colors.YELLOW,
                "max_iters": typer.colors.YELLOW,
                "error": typer.colors.RED,
                "cancelled": typer.colors.RED,
            }.get(result.kind.value, typer.colors.WHITE)
            typer.secho(
                f"结果: {result.kind.value} | 轮次: {result.iterations} | "
                f"tokens: in={result.usage['input_tokens']} out={result.usage['output_tokens']}",
                fg=kind_color,
                bold=True,
            )
            if result.awaiting:
                typer.secho(f"等待: {result.awaiting}", fg=typer.colors.YELLOW)
            if result.error:
                typer.secho(f"错误: {result.error}", fg=typer.colors.RED)
        finally:
            await client.close()

    asyncio.run(_run())


@app.command()
def demo_survey(
    workspace: str = typer.Option(
        "", "--workspace", "-w", help="工作区目录 (空=用 data_dir/demo_survey_ws)"
    ),
    host: str = typer.Option("0.0.0.0", "--host", help="监听 (0.0.0.0=局域网可访问)"),
    port: int = typer.Option(8000, "--port", "-p", help="端口"),
    serve: bool = typer.Option(True, "--serve/--no-serve", help="入库后是否自动起服务"),
) -> None:
    """一键预置百篇综述调研并 (可选) 起服务, 给出可分享链接。

    流程:
      1. 初始化 SQLite (建表)
      2. 把 articles/survey-baijian-zongshu/{main.tex, references.bib} 入库为 artifact
         + copy 到 workspace (供 PaperView 渲染 + 下载 tex)
      3. (默认) 启动 API + 前端服务, 打印局域网可访问的链接

    朋友访问 http://<你的局域网IP>:<port>/ 即可看到前端;
    在工作台创建会话后, 工作区会出现 main.tex, 点击"查看论文"进入 PaperView。
    """
    import asyncio
    import socket

    from operon.config import load_settings
    from operon.db.session import init_engine, session_factory
    from operon.scripts.ingest_survey import ingest_survey

    settings = load_settings()
    ws = Path(workspace).resolve() if workspace else (settings.data_dir / "demo_survey_ws")
    ws.mkdir(parents=True, exist_ok=True)

    async def _ingest():
        engine = await init_engine(settings.db_url())
        factory = session_factory(engine)
        result = await ingest_survey(workspace=ws, db_session_factory=factory)
        await engine.dispose()
        return result

    typer.secho("正在入库百篇综述调研...", fg=typer.colors.CYAN)
    result = asyncio.run(_ingest())
    typer.secho(f"✓ 已入库到 SQLite: {settings.db_url()}", fg=typer.colors.GREEN)
    typer.secho(f"  workspace: {result['workspace']}", fg=typer.colors.GREEN)
    typer.secho(f"  project_id: {result['project_id']}", fg=typer.colors.GREEN)
    typer.secho(f"  files: {[f['path'] for f in result['files']]}", fg=typer.colors.GREEN)

    # 局域网 IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    typer.echo("")
    typer.secho("── 可分享链接 ──", fg=typer.colors.BLUE, bold=True)
    typer.secho(f"  工作台: http://{lan_ip}:{port}/  (需创建会话)", fg=typer.colors.YELLOW)
    typer.secho(f"  本机:   http://127.0.0.1:{port}/", fg=typer.colors.YELLOW)

    if not serve:
        typer.secho("\n跳过启动服务 (--no-serve)。手动起: operon serve", fg=typer.colors.CYAN)
        return

    # 启动服务, 在服务就绪后自动创建一个指向 demo workspace 的 session,
    # 打印直达论文预览页的链接 (朋友点链接即可看渲染 + 下载 tex, 无需填 API key)。
    import uvicorn


    config = uvicorn.Config("operon.api.app:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def _serve_with_demo_session():
        # 服务任务
        serve_task = asyncio.create_task(server.serve())
        # 等 lifespan 跑完 (manager 就绪)
        await asyncio.sleep(2.5)
        # 建 demo session
        try:
            # 拿到 lifespan 创建的 manager (通过 app.state)
            from operon.api.app import app as _app
            from operon.api.sessions import SessionManager  # 复用 app 里的 manager
            from operon.llm.openai_compat import OpenAICompatClient
            manager: SessionManager = getattr(_app.state, "manager", None) or SessionManager()
            client = OpenAICompatClient(base_url="http://localhost", api_key="demo", model="demo")
            active = await manager.create(
                llm=client, workspace=ws, model="demo", context_window=None
            )
            demo_sid = active.id
            typer.echo("")
            typer.secho("✓ 已创建预置会话 (朋友点此链接直达论文页):", fg=typer.colors.GREEN, bold=True)
            typer.secho(f"  http://{lan_ip}:{port}/paper/{demo_sid}", fg=typer.colors.CYAN, bold=True)
            typer.secho(f"  http://127.0.0.1:{port}/paper/{demo_sid}", fg=typer.colors.CYAN)
            typer.echo("  (Ctrl+C 退出服务)")
        except Exception as e:
            typer.secho(f"预置会话创建失败 (不影响工作台手动用): {e}", fg=typer.colors.RED)
        await serve_task

    try:
        asyncio.run(_serve_with_demo_session())
    except KeyboardInterrupt:
        typer.secho("\n服务已停止", fg=typer.colors.CYAN)


if __name__ == "__main__":
    app()
