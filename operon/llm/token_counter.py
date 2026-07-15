"""Token 计数。

对应原版: 0848.js 用 text.length / d8 (d8=4) 估算 token。
本模块提供两种计数:
1. 字符估算 (CHARS_PER_TOKEN=4,与原版一致,无外部依赖,用于 Rolling Compact 触发判断)
2. tiktoken 精确计数 (用于计费/预算,国内模型可用 cl100k 近似)

设计: Rolling Compact 优先用字符估算 (与原版行为一致、零依赖、可重现);
精确计数作为可选增强。
"""

from __future__ import annotations

# 与原版 0836.js:417 严格一致
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """字符估算 token 数。对应原版 Math.floor(text.length / d8)。

    原版用法 (0848.js:347): output + Math.floor(G / d8)
    """
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


class TokenCounter:
    """token 计数器。

    精确模式用 tiktoken (cl100k_base),国内模型按 cl100k 近似。
    若 tiktoken 不可用 (无网络下载编码) 则降级到字符估算。
    """

    def __init__(self, use_tiktoken: bool = False):
        self._enc = None
        if use_tiktoken:
            try:
                import tiktoken

                self._enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._enc = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text))
        return estimate_tokens(text)
