# 原版文件 ↔ operon-py 模块对照表

反编译源码位于 `claude-science-decompiled/03-decoded/`。本表维护原版混淆文件 → 本项目 Python 模块的对应关系，是"对照原版"原则的索引。行号针对反编译产物。

## 核心架构

| 原版文件 | 原版符号/职责 | operon-py 模块 | 说明 |
|---|---|---|---|
| `00-runtime.js` | Bun 模块注册/懒加载入口 | （无对应，Python 原生 import） | Python 无需此层 |
| `0221.js` (711KB) | 业务大脑：host SDK、provenance、Python kernel worker | `operon/tools/host.py`、`operon/kernel/worker.py`、`operon/citations/` | 拆分；provenance → citations |
| `2558.js` / `2547.js` | CLI 入口 + serveMain | `operon/cli/main.py` | 简化为纯后端库 + CLI |

## Agent 状态机（阶段 1）

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0871.js` (106KB) | `Ki` 类 — 会话编排器、主循环 `_runLoop`、所有门控、biosecurity、plan-mode | `operon/agent/runner.py` |
| `0858.js` (128KB) | `x$_` 类 — 单次 LLM 调用包装、rolling-compact 执行、checkpoint 用量 | `operon/agent/conversation.py` |
| `0854.js` (150KB) | `_$_` 类 — delegate 引擎、子 frame、结构化输出投递 | `operon/agent/delegator.py` |
| `0865.js` (139KB) | configurator — 工具目录、system prompt 拼装、generate_plan/approve_plan | `operon/prompts/registry.py`、`operon/tools/builtins/plan.py` |
| `0808.js` | 工具实现：search_skills/memory/ask_user/skill | `operon/tools/builtins/` |

## Frame 树（阶段 1）

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0110.js:51-133` | `frames` 表定义 (Drizzle `P`) | `operon/db/schema.py::Frame`、`operon/frames/model.py` |
| `0125.js:55,83` | createRootFrame / createChildFrame | `operon/frames/service.py` |
| `0877.js:207` | FrameService 类 | `operon/frames/service.py` |
| `0880.js:229,310` | fork（仅根 frame） | `operon/frames/tree.py` |
| `0011.js:126-137` | FrameStatus 枚举 (`Hc_`) | `operon/agent/states.py` |

## Rolling Compact（阶段 2）

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0848.js` (72KB) | `newRollingCompactState`/`checkRollingCompact`/`abortRollingCompact` | `operon/compact/engine.py`、`operon/compact/state.py` |
| `0836.js:417` | `d8 = 4` (CHARS_PER_TOKEN) | `operon/compact/constants.py` |
| `0848.js:2342-2357` | 全部 RC 常数赋值 | `operon/compact/constants.py` |
| `0023.js:7-20` | 消息类型枚举 (RC_FOLD_L1/L2 等) | `operon/compact/types.py` |
| `0039.js:304-309` | RC 配置默认值 | `operon/config.py` |

### Rolling Compact 常数对照（已逐条核实）

| 常数 | 原版值 (0848.js) | operon-py |
|---|---|---|
| `CHARS_PER_TOKEN` (d8) | `4` (0836.js:417) | `CHARS_PER_TOKEN = 4` |
| `OUTPUT_CEILING` (aSz) | `32000` (2356) | `OUTPUT_CEILING = 32000` |
| `MIN_CHUNK_TOKENS` (Xx_) | `4096` (2346) | `MIN_CHUNK_TOKENS = 4096` |
| `MAX_FORK_FAILURES` (po) | `3` (2357) | `MAX_FORK_FAILURES = 3` |
| `L2_PREFIX_BUDGET_RATIO` (lSz) | `0.4` (2345) | `L2_PREFIX_BUDGET_RATIO = 0.4` |
| `KB_RATIO` (cSz) | `0.7` (2343) | `KB_RATIO = 0.7` |
| `KA_FLOOR` (mSz) | `50000` (2342) | `KA_FLOOR = 50000` |
| `DEGENERATE_DRAFT_RATIO` (dSz) | `0.25` (2349) | `DEGENERATE_DRAFT_RATIO = 0.25` |
| `KF_PERCENT` (R_G) | `50` (2344) | `KF_PERCENT = 50` |
| `rc_context_ceiling` | `500000` (0039.js:307) | `context_ceiling = 500000` |
| `ka_ratio` | `0.2` (0039.js:306) | `ka_ratio = 0.2` |

## 验证 Harness（阶段 3）

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0850.js` (139KB) | `oL_` Verifier — checkpoint/shadow/dispatcher | `operon/verify/verifier.py` |
| `0850.js:2023` | `spawnReviewersAndAwait` | `operon/verify/reviewer.py` |
| `0850.js:1173` | `_spawnBookmarkerAndAwait` | `operon/verify/bookmarker.py` |
| `0871.js:1569-1596` | `_maybeInvalidateSubmittedOutput` (max 2 bounce) | `operon/verify/invalidation.py` |
| `0233.js:49` | `Ls_ = 2` 最大失效次数 | `operon/verify/invalidation.py::MAX_OUTPUT_INVALIDATIONS` |
| `0110.js:977-1007` | `verification_checks` 表 | `operon/db/schema.py::VerificationCheck` |
| `0039.js:223-229` | `[verification]` 配置 | `operon/config.py` |

## Artifact 版本（阶段 4）

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0187.js:47-169` | `_saveArtifactCommon` 乐观并发 | `operon/artifacts/store.py` |
| `0110.js:302-419` | `artifacts` + `artifact_versions` 表 | `operon/db/schema.py` |
| `0110.js:431` | `artifact_dependencies` DAG | `operon/artifacts/lineage.py` |
| `0249.js:119` | `{{artifact:<VID>}}` marker 语法 | `operon/artifacts/marker.py` |
| `0862.js:44-68` | `version_of` 验证 | `operon/artifacts/version.py` |

## 提示词

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0195.js` (98KB) | 提示词常量宝库 (KtO/qtO/ytO/RULES_* 等) | `operon/prompts/rules/*.py` |
| `0198.js:7` | `AtO` 注册表 + `cz()`/`wV_()` 模板 | `operon/prompts/registry.py` |
| `0200.js:14` | `cz_()` floor 拼装 | `operon/prompts/registry.py::build_floor` |
| `0201.js:5` | `cYw` floor 5 keys | `operon/prompts/registry.py::FLOOR_KEYS` |
| `0860.js:618-697` | stable/dynamic 段拼装 | `operon/prompts/registry.py` |

## 模型与配置

| 原版文件 | 原版符号/职责 | operon-py 模块 |
|---|---|---|
| `0234.js:16` | `dF` 模型分级 (haiku/sonnet/opus) | `operon/config.py` (改为 small/medium/large 通用名) |
| `0039.js` | Zod 配置 schema | `operon/config.py` (Pydantic) |
| `0038.js` | release 固化配置 | `operon/config.py` (环境变量覆盖) |
| `0202.js:501` | `A3` LLM 客户端 + 凭证解析 | `operon/llm/` |

## 工具面

| 原版文件 | 工具 | operon-py 模块 |
|---|---|---|
| `0861.js:21-47` | `YEz` 工具注册集 | `operon/tools/registry.py` |
| `0336.js` | bash/python/r/repl/compute_provider | `operon/tools/builtins/exec.py` |
| `0491.js` | read_file / save_artifacts | `operon/tools/builtins/files.py`、`operon/tools/builtins/artifacts.py` |
| `0802.js` | fetch_article_fulltext | `operon/tools/builtins/literature.py` |
| `0808.js` | search_skills/memory/ask_user/skill | `operon/tools/builtins/` |
| `0814.js:174-261` | host-call dispatcher | `operon/tools/host.py` |
| `0815.js:49-109` | host 方法集 (o$z/baw/haw/faw) | `operon/tools/host.py` (按 kernel kind 限制) |
