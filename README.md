# Axiom

Axiom 是一个本地科研 AI 工作台，基于 operon-py 内核构建，支持文献综述、数据分析、数学建模与代码实验。

## 特性

- **本地优先**：所有数据保存在本机，支持 SSD 数据目录软链。
- **桌面客户端**：基于 Tauri 的 macOS 应用，启动即进入工作台。
- **内置设置面板**：无需编辑配置文件，在 APP 内管理 LLM Provider、MCP Server 与数据源。
- **多 Provider / 多 MCP**：可配置多个 LLM Provider（仅启用一个生效），多个 MCP Server（独立开关）。
- **论文预览**：自动生成并渲染 LaTeX 综述，支持选择工作区中的任意 `.tex`。
- **可调整布局**：左右侧边栏可拖动宽度、可隐藏。
- **产物管理**：工作区文件支持删除。

## 项目结构

```
operon/          # Python 后端内核
frontend/        # React + Vite 前端
src-tauri/       # Tauri 桌面壳
scripts/         # 构建脚本
skills/          # Agent skills
tests/           # 测试
```

## 开发

```bash
# 安装 Python 依赖
uv sync -e .

# 运行测试
uv run pytest

# 启动后端服务
uv run operon serve

# 启动前端开发服务器
cd frontend && bun run dev
```

## 打包 Mac APP

```bash
./scripts/build_mac_app.sh
```

产物：

- `src-tauri/target/release/bundle/macos/Axiom.app`
- `src-tauri/target/release/bundle/dmg/Axiom_0.0.1_aarch64.dmg`

## 配置

首次启动时，APP 会自动从 `config.toml`（若存在）导入配置，之后所有设置保存在 APP 内的设置面板中，持久化到 `~/.axiom/settings.json`。`config.toml` 本身已被 `.gitignore` 忽略，不会进入 Git。

## 许可

MIT
