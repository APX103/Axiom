# Axiom

> **Languages:** [English](README.md) · **简体中文**

Axiom 是一个**本地优先的科研 AI 工作台**，基于 `axiom-core` 内核构建，支持文献综述、数据分析、数学建模与代码实验。它借鉴了 [Deli Chen 的 auto-research 协议](TODO-补链接) 来支撑长时程无人值守的研究循环。

- **本地优先**：所有数据、会话、工作区文件、SQLite 数据库都保存在本机。
- **云端推理 + 本地执行**：LLM 调用走兼容 OpenAI 协议的云端/本地模型；工具执行、文件操作、代码运行全部在本地完成。
- **桌面客户端**：基于 Tauri v2 的 macOS 应用，启动即进入工作台。
- **学术工作流**：内置 OpenAlex 论文检索、引用图扩展、DOI 校验；一条从文献评分到 PDF 排版的论文写作链（`paper-writing` 编排五个子 skill）。
- **LaTeX 模板与 PDF 预览**：内置 IEEE / ACM / 通用 article 模板（preamble 锁定，编译不报错），TeX(KaTeX) 与 PDF(Tectonic 真编译) 双模预览。
- **长时程自主研究**：借鉴 Deli Chen 的 auto-research 协议（`deli-autoresearch` skill），支持数小时无人值守的研究循环。

---

## 目录

- [快速开始](#快速开始)
- [安装](#安装)
- [首次配置](#首次配置)
- [使用指南](#使用指南)
  - [聊天与工具调用](#聊天与工具调用)
  - [Plan Mode](#plan-mode)
  - [文献综述](#文献综述)
  - [LaTeX 论文预览](#latex-论文预览)
  - [工作区与 Artifact](#工作区与-artifact)
- [配置详解](#配置详解)
  -[`config.toml`](#configtoml)
  - [APP 内设置面板](#app-内设置面板)
  - [环境变量](#环境变量)
- [构建与开发](#构建与开发)
- [发布流程](#发布流程)
- [自动更新检查](#自动更新检查)
- [故障排查](#故障排查)
- [项目结构](#项目结构)
- [架构文档](#架构文档)
- [许可](#许可)

---

## 快速开始

1. 从 [GitHub Releases](https://github.com/APX103/Axiom/releases) 下载对应架构的 `.dmg`。
2. 打开 `.dmg`，把 `Axiom.app` 拖到 **应用程序** 文件夹。
3. 第一次启动时按住 `Control` 键右键打开（未签名应用需要放行 Gatekeeper）。
4. 进入设置面板，填入你的 LLM Provider（Base URL + API Key + Model）。
5. 关闭设置，开始使用。

> 如果你主要用来写文献综述，建议同时填入 **OpenAlex API Key**（免费，https://openalex.org/settings/api）。

---

## 安装

### macOS（推荐）

| 平台 / 架构 | 下载 |
|-------------|------|
| macOS Apple Silicon (M1/M2/M3/M4) | `Axiom_x.x.x_aarch64.dmg` |
| Linux x86_64 | `Axiom_x.x.x_amd64.deb` / `.AppImage` |

1. 挂载 `.dmg`，拖动 `Axiom.app` 到 `/Applications`。
2. 首次运行若提示“无法打开，因为无法验证开发者”，请：
   - 按住 `Control` 键，右键点击 `Axiom.app` → **打开** → **仍要打开**；或
   - 前往 **系统设置 → 隐私与安全性 → 安全性**，点击 **仍要打开**。
3. 如果之前通过终端安装过未签名版本，可能需要先清除隔离属性：
   ```bash
   xattr -cr /Applications/Axiom.app
   ```

### 从源码运行（开发）

见 [构建与开发](#构建与开发)。

---

## 首次配置

Axiom 启动后会自动在后台启动 Python 后端。首次启动时没有 LLM 配置，界面右下角会显示 **“服务离线”**，并自动弹出设置面板。

### 必填：LLM Provider

进入设置面板的 **Models** 标签页，添加一个 Provider 并启用：

| 字段 | 说明 |
|------|------|
| Name | 任意名称，如 `StepFun` / `DeepSeek` |
| Base URL | 兼容 OpenAI 的 API 地址，如 `https://api.stepfun.com/step_plan/v1` |
| API Key | 你的 API key |
| Model | 模型名，如 `step-3.7-flash` / `deepseek-chat` |
| Context Window | 模型实际上下文长度，如 `256000` |
| Max Tokens | 单次最大输出 token，如 `8192` |

填写后点击 **保存**。保存成功后，右下角状态会变为 **“服务就绪”**。

### 可选：联网搜索 MCP

在 **MCP** 标签页可添加支持 MCP 协议的搜索服务，例如智谱 `web-search-prime`：

- URL: `https://open.bigmodel.cn/api/mcp/web_search_prime/mcp`
- Headers: `Authorization: Bearer <你的智谱 key>`

### 可选：学术 API Keys

在 **Academic** 标签页填入：

- **OpenAlex API Key**：写文献综述时必需（OpenAlex 自 2026-02 起强制要求 key）。
- 其他数据源 key 按需填写。

### 配置文件路径

APP 内设置会持久化到：

```text
~/.axiom/settings.json
```

开发者也可以直接编辑项目根目录的 `config.toml`（已被 `.gitignore` 忽略，不会进 Git）。首次启动时，APP 会自动从 `config.toml` 迁移配置到 `settings.json`。

---

## 使用指南

### 聊天与工具调用

在工作台底部的输入框中输入问题，Axiom 会调用合适的工具完成：

- 文件/目录操作（`read_file`、`write_file`、`edit_file`、`list_dir`）
- Python / Bash 执行（`python`、`bash`）
- 网页搜索（`web_search`、`fetch_url`）
- 论文搜索（`search_papers`、`fetch_paper`）
- MCP 工具（如果配置了 MCP server）

工具调用过程和结果会实时显示在对话流中。

### Plan Mode

启用 Plan Mode 后，Axiom 会先制定一个多步骤计划，等你批准后再执行。适合复杂任务。

- 在设置面板的 **General** 标签页开启 **Plan Mode**。
- 也可以在输入框中使用 `/plan ` 前缀临时开启单条 plan mode。

### 文献综述

输入类似以下提示词即可触发 literature-review skill：

```text
帮我写一份关于 "chain-of-thought reasoning in LLMs" 的综述
```

Axiom 会执行：

1. 用 OpenAlex 做首轮文献 sweep。
2. 取 Top 命中做前后向引用图扩展（`expand_citations`）。
3. DOI 校验与撤稿检测。
4. 按主题合成写作，保存为 markdown artifact。

> 必须有 OpenAlex API Key 才能使用 OpenAlex 检索功能。

### LaTeX 论文预览

如果工作区中有 `.tex` 文件，左侧工作区面板会显示 **“查看论文”** 按钮。PaperView 提供两种预览模式：

- **TeX 模式**：解析 `.tex` 结构（章节、公式、引用），读同名 `.bib` 生成编号参考文献，用 KaTeX 渲染数学公式。轻量、即时。
- **PDF 模式**：用 [Tectonic](https://tectonic-typesetting.github.io/)（自包含 TeX 引擎，自动跑 bibtex + 多趟编译，按需从 CTAN 下载宏包）编译出真正的 PDF，再用 PDF.js 渲染。排版精确，等于 `pdflatex` 的产出。编译失败时会显示从 `.log` 解析出的具体错误行。

> PDF 编译需要本地装有 Tectonic：`brew install tectonic`。

### 论文模板

新建会话时可在侧栏选择论文模板，会把模板的 `main.tex`（preamble 已就位）复制进工作区：

- **通用 article**（单栏）
- **IEEE 会议**（IEEEtran，双栏）
- **IEEE 期刊**（IEEEtran，双栏）
- **ACM**（acmart，双栏）

模板 preamble 是锁定的——agent 只能填正文，不能改 `\documentclass` 或删除宏包。这同时解决了“格式不可控”（用官方 `.cls`）和“编译老报错”（TikZ 库等已预加载）。自定义模板：把 `{id}/template.tex` + `meta.json` 放到 `~/.axiom/templates/` 即可。详见 [架构文档](docs/ARCHITECTURE.md#模板templates)。

### 工作区与 Artifact

每个会话有独立的工作区目录。Agent 生成的文件首先落在工作区中；调用 `save_artifacts` 后，文件会被提升为版本化 Artifact，支持：

- 多版本历史
- 依赖 DAG
- `{{artifact:VID}}` 引用标记

工作区文件和 Artifact 默认保存在 `~/.axiom/workspaces/` 下。

---

## 配置详解

### `config.toml`

项目根目录的 `config.toml` 是开发者友好的配置方式，**已被 `.gitignore` 忽略**，不会进入 Git。

```toml
host = "0.0.0.0"
port = 8000

[models.large]
model = "step-3.7-flash"
base_url = "https://api.stepfun.com/step_plan/v1"
api_key = "sk-..."
max_tokens = 8192
context_window = 256000

[[mcp_servers]]
name = "web_search_prime"
url = "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"
[mcp_servers.headers]
Authorization = "Bearer ..."

[api_keys]
OPENALEX_API_KEY = "..."
```

配置优先级：

```text
环境变量 (AXIOM_*) > ~/.axiom/settings.json > config.toml > 代码默认值
```

### APP 内设置面板

普通用户推荐通过 APP 内设置面板管理：

- 多 LLM Provider（仅启用一个生效）
- 多 MCP Server（独立开关）
- API Keys
- Plan Mode / Disabled Skills
- Workspace 路径

### 环境变量

部分常用环境变量：

| 变量 | 作用 |
|------|------|
| `AXIOM_DATA_DIR` | 数据目录路径，默认 `~/.axiom` |
| `AXIOM_MODELS__LARGE__API_KEY` | LLM API Key |
| `AXIOM_MODELS__LARGE__BASE_URL` | LLM Base URL |
| `AXIOM_MODELS__LARGE__MODEL` | 模型名 |
| `OPENALEX_API_KEY` | OpenAlex API Key |

---

## 构建与开发

### 环境要求

- macOS 10.13+
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [bun](https://bun.sh/)
- [Rust](https://rustup.rs/)（Tauri 需要）

### 安装依赖

```bash
uv sync
cd frontend && bun install
```

### 启动后端服务

```bash
uv run axiom serve
```

### 启动前端开发服务器

```bash
cd frontend
bun run dev
```

### 运行测试

```bash
uv run pytest
```

### 打包 Mac App

```bash
./scripts/build_mac_app.sh
```

产物：

```text
src-tauri/target/release/bundle/macos/Axiom.app
src-tauri/target/release/bundle/dmg/Axiom_0.0.x_aarch64.dmg
```

> 如果本地 `.dmg` 打包因残留挂载卷失败，先执行 `hdiutil detach "/Volumes/dmg.xxx" -force` 清理。

---

## 发布流程

> 完整规则见 [AGENTS.md](AGENTS.md)。要点：

1. 版本号来自单一源：`package.json` / `pyproject.toml` / `Cargo.toml` / `tauri.conf.json` 四处必须一致，每次发版同步 bump（patch 优先）。`uv.lock` / `Cargo.lock` / `package-lock.json` 同步。
2. dev 分支上的改动 squash 成**一个干净的 release commit** 合到 `main`，打 annotated tag `v0.0.x` 并推送。
3. GitHub Actions 自动触发 [Release 工作流](.github/workflows/release.yml)：
   - CI 先做**版本一致性检查**（四处版本与 tag 不符则失败）。
   - 在 `macos-latest` 构建 `aarch64-apple-darwin`（`.dmg`）。
   - 在 `ubuntu-22.04` 构建 `x86_64-unknown-linux-gnu`（`.deb` / `.AppImage`）。
   - **直接发布正式 Release**（非 draft），并上传 `latest.json` 供 app 更新检查。
   - `_releases/latest/download/latest.json` 必须挂在正式 Release 上，draft 会在该 URL 下 404，导致 app 永远检查不到更新。

> 所有正式发布都从 `main` 分支打 tag。`main` 是唯一的长期发布分支；日常开发在 `dev`。

---

## 自动更新检查

Axiom 启动后会每小时检查一次 GitHub Releases 的最新版本（读 release 上的 `latest.json`，避免 GitHub API 限流）。如果发现新版本，顶部会显示横幅：

```text
发现新版本 v0.0.x，当前版本 0.0.x。  [去 GitHub 下载]  [✕]
```

点击 **“去 GitHub 下载”** 会在浏览器打开对应 Release 页面，由用户手动下载安装。

> 当前版本号从 `package.json` 读取（单一源），发版时 bump 四处版本号即可。

---

## 故障排查

### 关闭 App 后后端仍在运行 / 端口被占用

已修复。当前版本在关闭主窗口或退出 App 时会杀掉后端整个进程组。如果你遇到旧版本残留：

```bash
lsof -i :17896
kill -9 <pid>
```

### 打开 App 显示“服务离线”

1. 确认右下角状态是否为“服务离线”。
2. 打开 **设置面板**，确认已保存有效的 LLM Provider。
3. 如果之前手动 kill 过后端，等待 2-3 秒，前端会自动重连。
4. 仍不行则从终端启动 App 看后端日志：
   ```bash
   /Applications/Axiom.app/Contents/MacOS/Axiom
   ```

### 文献综述无法检索 / 报 OpenAlex key 错误

- 在设置面板 **Academic** 标签页填入 OpenAlex API Key。
- 或设置环境变量 `OPENALEX_API_KEY`。

### 打包后的 App 在同事电脑上打不开

1. 确认架构匹配：Apple Silicon 包不能运行在 Intel Mac 上。
2. 让同事用 `Control + 右键 → 打开` 放行 Gatekeeper。
3. 如果报 `HTTPX SOCKS` 相关错误，请使用最新版 `scripts/build_mac_app.sh` 重新打包（已修复 `socksio` hidden import）。

### DMG 打包失败

本地开发时若 `bundle_dmg.sh` 失败，通常是上一次打包残留了挂载卷：

```bash
hdiutil info | grep dmg
hdiutil detach "/Volumes/dmg.xxxxxx" -force
```

GitHub Actions 使用干净 runner，一般不会出现此问题。

---

## 项目结构

```text
Axiom/
├── axiom_core/              # Python 后端内核（Agent、API、工具、记忆、验证、存储）
├── frontend/            # React + TypeScript + Vite 前端
├── src-tauri/           # Tauri v2 桌面壳（Rust）
├── skills/              # Agent skills（paper-writing / lit-survey / deli-autoresearch …）
├── templates/           # 论文模板（article / IEEE / ACM，每套一个 .tex + meta.json）
├── tests/               # pytest 测试
├── scripts/             # 构建脚本
├── docs/                # 设计文档 + ARCHITECTURE.md
├── config.example.toml  # 配置模板
├── backend_entry.py     # PyInstaller 打包入口
└── axiom-backend.spec  # PyInstaller 规格文件
```

---

## 架构文档

想了解内部实现（agent 循环、frame 状态机、Rolling Compact、验证 harness、内置 skills 的分工、LaTeX→PDF 链路），请看 **[架构文档](docs/ARCHITECTURE.md)**。

---

## 许可

MIT
