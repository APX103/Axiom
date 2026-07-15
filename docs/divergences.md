# 故意偏离原版的设计点

记录所有 operon-py **故意偏离** Operon 原版的设计决策，及理由。这是"诚实记录偏离"原则的载体——偏离不是 bug，是有意为之，但必须可追溯。

## 1. LLM Provider：Anthropic → OpenAI 兼容

**原版**：`0234.js:16` 硬编码 `dF = {anthropic: {small/medium/large}}`，客户端 `A3` (`0202.js:501`) 只对接 Anthropic API。

**本项目**：`operon/llm/` 提供 Provider 抽象，首版实现 OpenAI 兼容 adapter，通过 `base_url` 适配国内模型（DeepSeek、通义、智谱、Moonshot 等）。

**理由**：用户需求是接入国内模型。OpenAI 兼容协议是国内模型的事实标准。

**影响**：必须实现消息格式转换层（见 §2），无法使用 Anthropic 原生特性（见 §3、§4）。

## 2. 消息格式：tool_use ↔ function_calling 转换

**原版**：Anthropic `tool_use` / `tool_result` content block 格式。

**本项目**：`operon/llm/message_adapter.py` 做双向转换。对外暴露统一的内部消息模型（接近 Anthropic 语义，因为 agent 状态机依赖 tool_use 语义），与 OpenAI 兼容 API 通信时转换。

**理由**：保持 agent 状态机对原版的忠实（`_processLlmResponse` 依赖 tool_use 分支），同时适配国内模型。

## 3. 引用闭环：Anthropic Citations → 自建 RefCheck ⭐

**原版**：依赖 Anthropic Citations API（`citations_delta` 事件，`0155.js`/`0166.js` 解析），模型自动产出引用。

**本项目**：`operon/citations/refcheck.py` 自建引用闭环——claim 抽取 → grounding 校验 → 三档 verdict（pass/warn/fail）→ 可疑来源分类。

**理由**：国内模型无等价 API。**这反而是改进**——引用闭环不再绑死 provider，且对应 `survey-pipeline-design.html` 中设计的 RefCheck 思路。原版的 `suspect_citations` 隔离逻辑（`0850.js`）会保留并增强。

## 4. PDF 处理：双路径 → 强制文本路径（首版）

**原版**：两条路径——`read_file` 走 vision（PDF document block，每页 ~1600 vision token hint），`pdf-explore` skill 走文本提取（pypdfium2）。

**本项目**：首版**统一走文本提取路径**（pypdfium2，即 pdf-explore 那条）。vision 路径后置。

**理由**：国内模型对 PDF document block 的支持参差；文本路径成本更低、可持久化、可多轮引用，更适合论文写作场景（原版 `claude-science-deep-dive.html` §1.3 的结论也是写作场景应默认走文本路径）。

## 5. web_search：服务端工具 → 自建工具

**原版**：`0808.js:1024` 用 Anthropic 服务端 `web_search_20250305` server tool。

**本项目**：`operon/tools/builtins/web_search.py` 自建，封装搜索 API。

**理由**：国内模型无等价服务端工具。

## 6. 运行形态：daemon + 桌面壳 → 纯后端库 + CLI

**原版**：`2558.js` serve 子命令起 Fastify daemon（端口 8000/8765），桌面壳通过 vsock/stdio 通信。

**本项目**：纯后端库（`import operon`）+ CLI（`operon` 命令）。HTTP server 后置为可选层。

**理由**：用户明确选择。库形态更易测试、集成、迭代。

## 7. 语言/技术栈：Bun/TS/Drizzle/Zod → Python/SQLAlchemy/Pydantic

**原版**：TypeScript + Bun runtime + Drizzle ORM + Zod。

**本项目**：Python 3.11+ + SQLAlchemy 2.0 + Alembic + Pydantic + Pydantic-Settings。

**理由**：Python 全栈——kernel 无需跨语言 RPC（`host` 对象可进程内）、科研生态原生（numpy/pandas/scipy/pypdfium2）、用户技术栈偏好。

## 8. Python kernel 集成：跨语言 RPC → 进程内优先

**原版**：`0221.js` Python worker 是长驻子进程，通过 stdio JSON-per-line 协议与 TS 主进程通信（lockstep，单次在途调用，120s deadline）。

**本项目**：Python 全栈下 `host` 对象可进程内直接调用（性能更好）。仅隔离执行（沙箱）时起子进程，子进程仍用 stdio JSON 协议保留可隔离性。

**理由**：消除不必要的跨语言 RPC 开销，同时保留沙箱隔离能力。

## 9. 模型分级：固定三档 → 配置化三档

**原版**：`dF` 固定 `small=haiku / medium=sonnet / large=opus`，硬编码 Claude snapshot id。

**本项目**：`operon/config.py` 暴露 `small/medium/large` 三档，每档的 `model` 字符串 + `base_url` 可配。默认值留空，由用户填国内模型名。

**理由**：适配多 provider。

## 10. Artifact 落库：内存优先 + 可选 SQLite 持久层（阶段 4）

**原版**：`ArtifactStore`（`0187.js` `_saveArtifactCommon`）始终写 SQLite，三层表 artifacts / artifact_versions / artifact_dependencies（+ content_snapshots 去重）。

**本项目**：`operon/artifacts/store.py` 的 `ArtifactStore` 采用**内存优先**策略——`db_session_factory=None`（缺省）时纯内存 + 工作区文件（向后兼容现有测试与 CLI `run`）；API 服务（`operon serve`）的 lifespan 初始化 SQLite engine 并注入 `SessionManager`，此时 `save_async()` 同步写 DB（artifacts / artifact_versions / artifact_dependencies），`load_from_db()` 启动时回放内存支持断点续会话。

**理由**：
- 内核（agent 循环、host.lineage/query）只需内存态即可工作，DB 是可选持久层——这让 CLI `run` 和单元测试零 DB 依赖。
- DB 写失败不阻断 agent 循环（文件已落盘，内存态正确）——可用性优先于持久性。
- `artifact_dependencies` 表（DAG 边）为本阶段新增，承载 `get_lineage_topology`。
- `content_snapshots`（内容级去重）仍后置——首版用 `storage_path` 直接读工作区文件。

**影响**：
- `VersionRecord` 补了 `frame_id` 字段（修原 bug：`save()` 一直收到但没存）。
- `SaveResult` 补 `new_deps` 字段传递本次 save 新增的 DAG 边给 `_persist`。
- `artifact_tool.save_artifacts` 在有 DB 时调 `save_async`，否则纯内存 `save`。

## 11. 后置能力（首版不做，非永久偏离）

以下原版能力首版不实现，但架构预留接口，后续阶段按需补：
- **Biosecurity 轨迹审查**：`0203.js` 异步 trajectory screen + sticky refusal。科研非生物场景可后置；`operon/agent/runner.py` 的 SCREEN 钩子保留。
- **BYOC / remote compute**：Modal/SSH/infer provider。首版只本地 sandbox。
- **Skills marketplace + license 门控**：`0795.js`/`skill_license_assents`。首版只本地 skill 加载。
- **OAuth 订阅登录**：`2321.js` PKCE flow。首版用 API key。
- **prompt caching / extended thinking**：国内模型支持参差，首版不依赖，接口预留。
- **content_snapshots 内容去重**：阶段 4 后置项，首版用 storage_path 直读文件。

## 判定原则

新增偏离时自问：
1. 这是 LLM 差异导致的吗？（→ §1-5 类）
2. 这是技术栈差异导致的吗？（→ §6-9 类）
3. 这是范围裁剪吗？（→ §10 类，必须标注"后置"而非"不做"）
4. 还是偷懒？**偷懒不算偏离，算 bug**——要么照搬，要么记为后置。
