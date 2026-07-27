# Axiom 架构

> 本文档面向想理解 Axiom 内部实现的开发者和贡献者。如果你只想使用 Axiom，请看 [README](../README.md) / [中文 README](../README.zh.md)。

## 一句话概括

Axiom 是一个**本地优先的科研 AI 工作台**：一个 Python agent 内核（`axiom_core`）负责推理、工具调用、记忆、验证；一个 React 前端做对话与论文预览；一个 Tauri 壳把它们打包成 macOS 桌面应用。LLM 推理走云端（兼容 OpenAI 协议），**工具执行与所有数据都留在本地**。

---

## 整体分层

```
┌─────────────────────────────────────────────────────────┐
│  Tauri 桌面壳 (src-tauri/, Rust)                         │
│  - 拉起/守护/清理 Python 后端进程                         │
│  - 注入后端端口到 webview                                  │
│  - 系统集成: Finder 定位、更新检查、毛玻璃                 │
├─────────────────────────────────────────────────────────┤
│  前端 (frontend/, React + TypeScript + Vite)              │
│  - SSE 事件流 → 对话/工具/计划/工作区 UI                    │
│  - PaperView: TeX(KaTeX) + PDF(PDF.js) 双模预览           │
│  - 设置: LLM Provider / MCP / 学术 API / 模板选择          │
├──────────────────── HTTP+SSE ────────────────────────────┤
│  FastAPI 层 (axiom_core/api/)                                │
│  - /sessions/{sid}/stream-sse  流式运行                   │
│  - /sessions/{sid}/compile      LaTeX→PDF 编译            │
│  - /templates                   论文模板                  │
│  - /files, /health, /settings …                          │
├─────────────────────────────────────────────────────────┤
│  Agent 内核 (axiom_core/agent/)                               │
│  - Runner: 主循环 (调 LLM → 工具 → 验证门控)               │
│  - Frames: 主/子 frame, 状态机 (PROCESSING/AWAITING…)      │
│  - Session: 工具注册 + 工作区 + 生命周期                    │
├─────────────────────────────────────────────────────────┤
│  能力层                                                    │
│  tools/   skills/   memory/   compact/   verify/         │
│  citations/  artifacts/  templates/  llm/  mcp/          │
├─────────────────────────────────────────────────────────┤
│  存储 (axiom_core/db/)  SQLite: 会话/消息/记忆/artifact/验证    │
└─────────────────────────────────────────────────────────┘
```

数据全部落在 `~/.axiom/`（若 SSD 路径存在则软链过去）：`workspaces/{sid}/` 存工作区文件，`axiom.db` 存结构化数据。

---

## 进程与生命周期

Tauri 主进程（Rust）启动时：

1. 找一个空闲端口，spawn `axiom-backend serve --port <port>`（PyInstaller 打包的单文件，或开发时 `python -m axiom_core.cli.main serve`）。
2. 把端口注入 webview 的 `localStorage`，前端据此发请求。
3. 主窗口关闭时杀掉整个后端进程组（`setpgid` + `killpg`），避免孤儿进程占端口。

详见 `src-tauri/src/lib.rs`。

---

## Agent 内核（`axiom_core/agent/`）

### Runner 主循环（`runner.py`）

每一轮：

1. **哨兵检查**：frame 是否取消 / 是否在等待（plan 审批 / ask_user）→ 是则提前返回。
2. **Rolling Compact**（见下）：压缩历史，控制 token。
3. 构建 system prompt + 投影消息（隐藏内部 harness-notice），调 LLM。
4. 处理响应：`max_tokens` 续传、空响应重试、`plan_produce_denial` 门（plan 模式下未生成计划不许结束）。
5. 若有 `tool_use` → 执行工具 → 结果入历史 → 检索熔断（连续纯检索超阈值则强制写作）。
6. 若无 tool_use → 自然完成路径：deep_review 产出门控、terminal barrier（reviewer 最终审查）。

### Frames（`frames/`）

会话由一组 frame 组成：一个根 MAIN frame + 按需 spawn 的子 frame（REVIEWER / BOOKMARKER）。状态机见 `agent/states.py`：`PROCESSING → (AWAITING_PLAN_APPROVAL | AWAITING_USER_RESPONSE) → PROCESSING → COMPLETED`。

### 关键设计：harness-notice

agent 内部会注入一些只给 LLM 看、不给用户看的提示（记忆召回块、max_tokens 续传提示、plan 拒绝、`[boundary]` 任务边界标记等）。这些消息带 `_harness_notice=True` 标记，持久化时编进 content JSON，前端渲染时跳过——所以用户看不到这些内部调度噪声。

---

## 能力层

### 工具（`tools/`）

内置工具（`tools/builtins/`）：文件读写、Python/Bash 执行、论文检索（OpenAlex/Crossref）、引用图扩展、DOI 校验、`compile_pdf`（Tectonic 编译）、`ask_user`、`boundary` 等。MCP 工具（`mcp/`）按配置动态挂载。

### Skills（`skills/`）

Skill 是**工具级的运行时配置**：一段 markdown 指令 + 可选 Python kernel，全局安装、所有会话共享。agent 通过 `search_skills({query})` 发现、`skill({skill: name})` 加载。来源：内置（随包携带）+ 用户自定义（`~/.axiom/skills/`）+ Claude 目录。

核心的论文写作链（见下文“内置 Skills”）是 Axiom 区别于通用 agent 的关键。

### 记忆（`memory/`）

跨会话的长期记忆：背景抽取（`extract.py`）+ BM25 召回（`recall.py`）。每轮新用户消息会触发召回，把相关记忆作为 `[Memory]` 块注入上下文。

### Rolling Compact（`compact/`）

长会话上下文管理：按 L1 chunk 切分历史，token 超阈值时折叠成摘要。`[boundary]` 标记（任务边界）会让 chunk 边界对齐到任务之间而非任务中间，避免把正在进行的任务从中间截断。

### 验证（`verify/`）

可选的 reviewer 子系统：阈值触发 checkpoint，spawn REVIEWER 子 frame 审查最近消息，产出 findings。terminal barrier 在自然完成时做最终审查，发现可修复问题则否决完成、强制再修一轮。

### Artifacts（`artifacts/`）

版本化产物存储：`save_artifacts` 把工作区文件提升为带版本的 artifact，支持多版本历史 + 依赖 DAG。

### 模板（`templates/`）

论文模板 = 一个 `.tex` 文件（preamble 锁定正确，含 `\documentclass` + 全部常用宏包 + 所有 TikZ 库）。内置 article / IEEE 会议 / IEEE 期刊 / ACM 四套（Tectonic 自动从 CTAN 拉 `.cls`）。`paper-writing` skill 硬约束 agent 不得改 preamble，只填正文——这同时解决了“格式不可控”和“编译老报错”。

---

## LaTeX → PDF

这是 Axiom 学术工作流的收尾环节，也独立于 agent 存在：

- **编译**：`compile_pdf` 工具 / `POST /sessions/{sid}/compile` 端点用 [Tectonic](https://tectonic-typesetting.github.io/)（自包含 TeX 引擎，自动跑 bibtex + 多趟，按需从 CTAN 下宏包）。
- **容错**：AI 写的 `.tex` 常因 TikZ 漏加载库而崩。`compile_tex` 会解析 `.log` 错误，若来自某个浮动环境（tikzpicture/algorithm/figure），临时把它包进 `\iffalse..\fi` 重编译，保证正文 PDF 至少能出来。
- **预览**：前端 PaperView 有 TeX/PDF 双 tab。TeX 模式用 KaTeX 做轻量渲染；PDF 模式编译后用 PDF.js 渲染真 PDF（排版精确）。

---

## 内置 Skills

Axiom 内置一组针对科研写作的 skill，彼此分工、由 `paper-writing` 编排：

| Skill | 职责 |
|-------|------|
| **paper-writing** | 编排器。协调下面五个子 skill，分四阶段（选题→起草→深度改进→冲刺）产出 8.0+ 综述。加载它 FIRST。 |
| **lit-survey** | 文献管线：高召回检索 + 确定性 LQS 多维评分（时效/引用/venue/机构/录用状态）+ A/B/C/D 引用深度分类 + arXiv→正式 venue 升级。产出分级的 `references.bib`。 |
| **paper-structure** | 综述骨架与论证逻辑：章节结构、四种段落逻辑模式（主张-证据-推论等）、MECE 分类法、形式化断言的对冲阶梯。 |
| **academic-figures** | 表现层：高信息密度表格（对比矩阵/benchmark/消融）、图表规范。 |
| **experiment-design** | 原创实验分析（仅当综述要 claim 时）：假设→变量→执行→结论的四阶段实验循环。 |
| **peer-review** | 多角色同行评审模拟，驱动迭代打分循环。 |
| **literature-review** | 通用文献检索与综述（比 lit-survey 轻，不强制 LQS 评分）。 |
| **deli-autoresearch** | **借鉴自 Deli Chen 的 auto-research 协议**，用于长时程无人值守研究循环（见下）。 |
| **pdf-explore** | 解析用户附带的 PDF/论文/报告，从内容里找答案。 |
| **figure-composer / figure-style / paper-narrative** | 多面板图组合、出版级图表规范、判断并重塑图表叙事。 |
| **skill-creator** | 创建/修改/衡量 skill 自身。 |

其余 skill（compute-env-setup / remote-compute-* / managed-model-endpoints / using-model-endpoint / self-awareness / product-self-knowledge / customize）面向远程计算与运行时自省，属较通用的工程能力。

### deli-autoresearch（长时程自主研究）

Axiom 借用了 **[Deli Chen 的 auto-research 协议](TODO-补链接)** 的思想，实现在 `skills/skills/deli-autoresearch/`。它针对代码 agent 跑长任务时的三类失败模式：

1. **认知循环**——在同一局部最优里打转；
2. **卡顿**——干完一段就停下等反馈，session 看着活着但没产出；
3. **运行时脆弱**——context compaction / 关 session 悄悄杀死循环。

核心机制是三层守护：**文件化状态**（`findings.jsonl` / `progress.json`，context 压缩后仍存活）、**stall 检测 + 结构性 pivot**（`stale_count≥2` 换结构而非调参，`≥4` 升级停止）、**guardian/worker 分离**（guardian 选方向并 delegate worker 产出可验证 finding）。

---

## 前端（`frontend/`）

- **`App.tsx`**：主工作台。左：会话/文件侧栏；中：对话流 + SSE 事件聚合（`hooks/useSession.ts`）；右：工作区 + 计划/验证面板。
- **`hooks/useSession.ts`**：SSE 事件流 → UI 状态（消息/工具调用/计划/artifact/awaiting）。包含 harness-notice 过滤、ask_user 选择框、stop/重发恢复等关键逻辑。
- **`components/PaperView.tsx`**：TeX/PDF 双模论文预览。
- **`tex.ts`**：轻量 LaTeX→HTML+KaTeX 解析器（PDF 模式不依赖它，走 Tectonic 真编译）。

---

## 构建与打包

- **后端**：PyInstaller（`axiom-backend.spec`）打成单文件 `axiom-backend`，skills/ + templates/ 一并塞进 bundle（解压到 `_MEIPASS`）。
- **前端**：`bun run build` → `frontend/dist/`，Tauri 嵌进 webview。
- **桌面壳**：`bun x @tauri-apps/cli build` 产出 `.app` / `.dmg`，`scripts/build_mac_app.sh` 再做 adhoc 签名 + entitlements。

---

## 测试

`tests/` 用 pytest，FakeLLM（按脚本返回响应）精确控制 agent 循环的每个分支。覆盖：自然完成、工具调用、max_tokens 续传、空响应重试、max iterations、ask_user 恢复、boundary harness-notice、compile_tex 容错、deli-autoresearch kernel 等。
