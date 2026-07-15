"""Rolling Compact 常数。

对应原版: 0848.js:2342-2357 + 0836.js:417 (d8) + 0039.js:304-309 (配置默认值)。
所有常数值逐条对照原版 (见 docs/mapping.md 常数表)。
任何改动都应在 docs/divergences.md 记录理由。
"""

from __future__ import annotations

# ===== 核心估算 =====
# 0836.js:417
CHARS_PER_TOKEN = 4  # d8: 字符→token 估算比

# ===== 预算与触发阈值 =====
# 0848.js:2342-2346
KA_FLOOR = 50000  # mSz: ka 最小值 (ka = max(KA_FLOOR, floor(budget*ratio)))
KB_RATIO = 0.7  # cSz: L1 触发 — 剩余预算 < ka * 0.7
L2_PREFIX_BUDGET_RATIO = 0.4  # lSz: L2 头部累积预算比例阈值
MIN_CHUNK_TOKENS = 4096  # Xx_: L1 chunk 最小 token

# 0848.js:2344
KF_PERCENT = 50  # R_G: (carry budget / ledger 行数 相关)

# ===== 输出与重试 =====
# 0848.js:2356-2357
OUTPUT_CEILING = 32000  # aSz: summarizer 输出 maxTokens 硬上限
MAX_FORK_FAILURES = 3  # po: 同 chunk 连续失败上限 → 升级 L2

# ===== 摘要质量门 =====
# 0848.js:2349-2352
DEGENERATE_DRAFT_RATIO = 0.25  # dSz: 退化判定比例 (摘要 < 上一最佳 * 0.25 且 < min)
DEGENERATE_DRAFT_DIVISOR = 100  # oSz: floor(chunk/100) — 退化判定最小 token 的除数
DEGENERATE_DRAFT_MIN_TOKENS = 200  # iSz: 退化判定最小 token 的下限
SECOND_PASS_DIVISOR = 6  # i8O: floor(chunk/6) — 二次压缩目标

# 0848.js:2347
GATE_MAX_RETRIES = 3  # lo: freeform summarizer 重试上限

# ===== 来自其他文件 (0821.js / 0858.js) =====
COMPACTION_TRIGGER_RATIO = 0.75  # Dgz: maybeStartCompaction 触发比例 (0821.js)
HARD_WALL_RATIO = 0.9  # D9_: hard-wall 比例 (0858.js)
MICROCOMPACT_RATIO = 0.65  # Rgz: microcompact 触发比例
ABSOLUTE_TOKEN_CEILING = 300000  # Tgz: 绝对 token 上限
PTL_RETRY_CAP = 32  # sSz (0848.js:2355 导出 PTL_RETRY_CAP): PTL(prompt-too-long) 重试上限

# ===== 压缩门 =====
# 0848.js:1497: budget = floor(chunk_tokens / 3); 门: final <= chunk_tokens/3
COMPRESSION_TARGET_DIVISOR = 3  # 摘要目标 = chunk / 3
COMPRESSION_GATE_DIVISOR = 3  # 通过门: final <= chunk / 3

# ===== 配置默认值 (0039.js:304-309) =====
DEFAULT_CONTEXT_CEILING = 500000  # rolling_compact_context_ceiling
DEFAULT_KA_RATIO = 0.2  # rolling_compact_ka_ratio


def compute_ka(budget: int, ka_ratio: float = DEFAULT_KA_RATIO) -> int:
    """计算 ka (压缩预算)。

    对应原版 T_G (0848.js:93-95):
        ka = max(KA_FLOOR, floor(budget * ka_ratio))
    """
    return max(KA_FLOOR, int(budget * ka_ratio))


def output_ceiling_for(chunk_tokens: int, model_max_output: int) -> int:
    """计算 summarizer 的输出 maxTokens。

    对应原版 _YO (0848.js:1121):
        max(1024, min(floor(chunk_tokens/3), model_max_output, OUTPUT_CEILING))
    """
    return max(1024, min(chunk_tokens // COMPRESSION_TARGET_DIVISOR, model_max_output, OUTPUT_CEILING))


def degenerate_min(chunk_tokens: int) -> int:
    """退化判定的最小 token 阈值。

    对应原版 (0848.js:1706): max(floor(chunk/100), DEGENERATE_DRAFT_MIN_TOKENS)
    """
    return max(chunk_tokens // DEGENERATE_DRAFT_DIVISOR, DEGENERATE_DRAFT_MIN_TOKENS)
