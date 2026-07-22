# Axiom 2026 H2 Roadmap

> 三件真正有品味的事。
>
> 起源：讨论"AI 时代科研验证的本质" → 讨论"AI 品味如何提升"
> → 用户问出最致命的问题："既然是 skills，我直接用 Codex + skills 不就好了吗？Axiom 凭什么存在？"
>
> 这个问题筛掉了之前所有 commodity 层的方向（idea-stress-test / claim-falsifier /
> experiment-design / 失败实验库 / 信息增益因果图 / 多 agent 共识 / Claim Verification
> Layer 等）。这些一旦被"Codex + skills 也能做"的标准过滤，全部出局。
>
> 剩下真正属于 Axiom 不可替代层的，只有 3 件。

---

## 核心立场

**Axiom 的护城河不在 skills 层，在基础设施层。**

一个 AI 工具的栈大致分四层：

```
Layer 4: Skills / Prompt 层     ← 可复制, 是 commodity
Layer 3: Agent 行为层            ← Codex 也有
Layer 2: 集成层 (LaTeX / 文献 API / workspace)  ← 有差异
Layer 1: 基础设施层 (记忆 / 长会话 / 编译)      ← 真护城河
```

Codex 的 Layer 1/2 是为通用编程场景调的。Axiom 的 Layer 1/2 是为科研场景调的。**这是差异化的根。**

**判断标准**：每个改动都要问"Codex + 同样的 skills 能不能做到？" 能做到的不做。
做不到的，才是真正值得做的。

---

## 三件该做的事

### 1. 跨会话记忆升级

**从"BM25 文本召回"升级成"结构化科研状态"。**

这是 Axiom 最深但最被忽视的护城河。Codex 会话结束就忘，Axiom 能跨 session 记得用户做过什么——**但现在的记忆层太弱，配不上这个护城河的地位**。

#### 现状

`operon/memory/` 是三层结构：
- `extract.py`: 每轮从对话里抽取记忆条目
- `recall.py`: BM25 + 关键词召回
- `store.py`: 存到 SQLite

本质是"用户提过 X"这种文本级别的召回。对"上周我们讨论过 kinase 142 位点"有用，但对"我整个 kinase 项目过去半年的演进"完全无能为力。

#### 目标：三层升级

**Layer A — 实体级记忆**（最优先，3 个月内）✅ 已完成 2026-07
- 不只是"用户提过 X"，而是把记忆拆成实体：
  - 项目（kinase 研究）
  - 主张（142 位点是催化必需）
  - 证据（A142F 突变实验）
  - 引用（Smith 2023）
  - 工具调用（跑过 ESMFold）
- 每个实体带时间戳、来源 session、置信度
- 查询从文本匹配升级为实体查询："kinase 项目里所有实验类证据"

> **Layer A.5 增补**（2026-07）：在 Layer A 之上加了 Project 作为产品层一等公民。
> 触发原因：同事反馈"开新 session 看到所有历史 session 的记忆"，BM25 串味。
> 做了三件事：(1) memories/sessions 表加 project_id 列 + 默认 project；
> (2) `/api/projects` CRUD；(3) 左栏 project 切换器 + session 按 project 过滤 +
> 记忆按 project 隔离（profile 层跨 project 共享，project/frame 层按 project_id 隔离）。
> 老数据（34 sessions + 40 memories）无感迁移到默认 project。

**Layer B — 时间感知**（6 个月内）
- 记忆带明确 timeline
- 能回答：
  - "我 3 个月前对 kinase 的判断是什么"
  - "我从 1 月到 6 月对 142 位点的看法怎么变的"
  - "上次更新这个主张是什么时候，之后有什么新证据"
- 科研是时间性的——半年前的判断可能因为新论文过期。**时间感知是科研记忆区别于聊天记忆的核心**。

**Layer C — 跨 session 因果**（12 个月内，最难但最值钱）
- session A 的结论影响了 session B 的方向。这种关联现在完全没记录
- 该有的关联：
  - "session B 是因为 session A 发现 X 才开的"
  - "session C 推翻了 session A 的结论 Y"
  - "session D 是 session B 的续作"
- 有了这个，用户能问"我这个项目是怎么演进到现在的"，而不是只能问"我上次说了什么"
- **这是 Codex 物理上做不到的（它根本不存跨会话数据）**

#### 为什么有品味
- 面向推进：让用户真正能"接续过去的研究"
- 不可替代：Codex 物理上做不到
- 可感知：用户第一次问"我 3 个月前怎么想的"得到准确回答时，立刻感受到差异
- less is more：不加新功能，只让已有功能变深

---

### 2. LaTeX 闭环产品化

**从"工具能编译"升级成"agent 主导的写-编译-看-改迭代循环"。**

Axiom 已经有 Tectonic + 锁定 preamble 模板 + log 解析，但这个闭环没被产品化。**Codex 能写 LaTeX，但不能编译、看 PDF、根据 PDF 改**。这个差距是 Axiom 的第二个不可替代基础设施。

#### 现状

- `compile_pdf` 工具能编
- PaperView 前端能看 PDF
- 锁定 preamble 解决了 TikZ 编译失败
- 浮动环境 `\iffalse` 容错
- **但这一切都是手动的**——用户要主动点"编译"、主动对比 PDF、主动决定改哪里。**agent 自己不会迭代**。

#### 目标：三个能力

**能力 A — diff-aware 编译**（3 个月内）
- 用户改了一段论文，系统只重编那段，不用整篇重编
- 对长论文（50+ 页）是巨大的体验提升——从 30 秒降到 2 秒
- 技术上：Tectonic 本身有 incremental compilation 支持，需要 workspace 状态管理
- 改 `compile_pdf` 工具加 `changed_only` 参数

**能力 B — agent 主导的迭代循环**（6 个月内，核心）
- 让 agent 自己跑"写 → 编译 → 看 PDF → 发现问题 → 改 → 再编译"
- 具体：
  ```
  agent 写完一段 →
    自动调 compile_pdf →
    解析 PDF + log →
    发现 "Figure 3 超出页面边界" →
    自动调整 \includegraphics 参数 →
    再编译 →
    通过 → 继续写下一段
  ```
- **这是 Codex 永远做不到的**——没有 Tectonic + 不能看 PDF + 没有 log 解析
- 这让 Axiom 真的能"自己写完一篇能编译的论文"

**能力 C — 编译错误自动修复**（1 个月内，最容易）
- 现在的 log 解析只是显示错误，该升级成根据错误自动建议/应用修复：
  - `Undefined control sequence \foo` → 检查是不是漏了 `\usepackage`
  - `Overfull \hbox` → 自动加 `\sloppy` 或调整断行
  - `Float too large` → 自动缩放图片
- 已有 log 解析基础，再加一个"错误 → 修复规则"的映射

#### 为什么有品味
- 面向推进：让 AI 真的能"完成"一篇论文（不只是写）
- 不可替代：Codex 物理上做不到
- 可感知：用户看到 agent 自己编译、自己改、自己再编译，立刻明白差异
- less is more：不加新功能，让已有功能闭环

---

### 3. 叙事和精力重新聚焦基础设施

**砍掉 commodity skills 的精力投入，叙事聚焦"长期科研基础设施"。**

最反直觉但最有品味的一点。**Axiom 现在最大的问题不是缺功能，是精力错配——大量投入在 commodity 层（skills/prompt），护城河层（基础设施）反而投入不够**。

#### 现状

Axiom 现有 skills（`lit-survey` / `literature-review` / `paper-writing` / `paper-structure` /
`paper-narrative` / `academic-figures` / `peer-review` / `experiment-design` /
`figure-style` / `figure-composer` / `pdf-explore`）。

**这 11 个 skills 里，至少 8 个是 Codex + 同样 SKILL.md 也能做的**。
它们的价值是"开箱即用降低门槛"，但不是"不可替代"。

**Axiom 在用 80% 的精力维护 commodity**。

#### 目标：三个做法

**做法 A — 叙事重新定位**（立刻）
- 现在 Axiom 的 README 给人印象是"AI 科研写作工具"
- 该改成 **"为长期科研调过的 AI 工作台，有不可替代的记忆和编译基础设施"**
- 具体：
  - README 第一屏不再是"写 survey"，而是"Axiom 记得你 3 个月前做过什么，Axiom 能真的编译你的 LaTeX"
  - Hero feature 不再是 paper-writing skill，而是跨会话记忆演示 + LaTeX 闭环演示
  - skills 从"主卖点"降级为"开箱即用的预设"

**做法 B — 每个 skill 都要绑定基础设施**（1 个月内）
- 给现有 skills 加"基础设施依赖标注"
- 强迫每个 skill 至少用到一项 Axiom 独有基础设施：
  - `paper-writing` → 必须用 LaTeX 闭环（不只是写 .tex 文件，要 compile iterate）
  - `lit-survey` → 必须用跨会话记忆（基于过去 survey 风格个性化）
  - `peer-review` → 必须用跨会话记忆（记得这篇论文的演进历史）
- 绑定之后，每个 skill 都成为"只有 Axiom 能跑出最佳效果"的 skill——Codex 拷过去能用，但效果差一截

**做法 C — 停止做新的 commodity skills**（持续）
- 之前推的 `idea-stress-test` / `claim-falsifier` / `experiment-design` / `related-work-archaeology` **全部停**
- 它们都是 commodity 层，Codex + skill 都能做
- 除非深度依赖 Layer 1（记忆）或 Layer 2（LaTeX），否则不做
- **新 skill 准入标准**："如果把这个 skill 拷到 Codex，效果会不会明显下降？" 不会就不做

#### 为什么有品味
- less is more 的极致体现：不是加东西，是**停止做错的事**
- 直击"用户凭什么用"的核心：让用户一秒钟看懂"Axiom ≠ Codex + skills"
- 不可替代：叙事聚焦在 Codex 物理做不到的事上
- 资源聚焦：把精力从 commodity 抽出来投到护城河

---

## 共同特征

这 3 点的共同特征：

- **都加强 Axiom 现有的不可替代层**（Layer 1 记忆 + Layer 2 LaTeX），不加 commodity 层（Layer 4 skills）
- **每一点都问"Codex 物理上能不能做到"**——答案都是不能
- **共同哲学：less is more**——不加功能，让已有功能变深；不做容易的事，做不可替代的事

## 优先级与依赖

```
[立刻]  叙事重新聚焦 (提升点 3, 做法 A)        ← 改 README/landing, 零代码
[1 月]  LaTeX 错误自动修复 (提升点 2, 能力 C)   ← 最容易, 立竿见影
[1 月]  Skills 绑定基础设施标注 (提升点 3, 做法 B)
[3 月]  实体级记忆 (提升点 1, Layer A)          ← 核心护城河
[3 月]  diff-aware 编译 (提升点 2, 能力 A)
[6 月]  时间感知记忆 (提升点 1, Layer B)
[6 月]  agent LaTeX 迭代循环 (提升点 2, 能力 B) ← 核心闭环
[12 月] 跨 session 因果 (提升点 1, Layer C)     ← 最值钱也最难
```

短期（1 月）做容易且立竿见影的；中期（3-6 月）做核心护城河（记忆 Layer A/B + LaTeX 闭环）；
长期（12 月）做最难的跨 session 因果。

---

## 被筛掉的方向（备忘）

以下方向都讨论过但被 "Codex + skills 也能做" 标准筛掉了，**不在这份 roadmap 里**：

- ~~`idea-stress-test` skill~~ — Codex + skill 能做
- ~~`claim-falsifier` skill~~ — Codex + skill 能做
- ~~`experiment-design-from-hypothesis` skill~~ — Codex + skill 能做
- ~~`related-work-archaeology` skill~~ — 大部分 Codex + skill 能做
- ~~失败实验库（独立产品）~~ — Codex + 自己造也能做
- ~~信息增益因果图（独立产品）~~ — 同上
- ~~多 agent 共识 / peer review 层~~ — 同上
- ~~Claim Verification Layer（对标 Evidence DAG）~~ — 本质是审计，面向展示
- ~~Evidence DAG 类似系统~~ — 审计过去，不推进未来
- ~~"sparring partner" 产品重定位~~ — 重做而非加, 且 Codex 也能做

这些方向的共同问题：**没有利用 Axiom 的不可替代基础设施（跨会话记忆 / LaTeX 闭环）**，
放在任何 agent 框架里都能做。所以不是 Axiom 该投入的方向。

如果未来某个方向演化出"深度依赖 Axiom 基础设施"的版本，再重新评估。

---

*文档日期：2026-07-20*
*路线图文档，非技术方案。具体实现按优先级单独立项。*
