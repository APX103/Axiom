"""BM25 + 关键词召回引擎。


纯 Python 实现, 不需要向量嵌入。
"""

from __future__ import annotations

import math
import re
from typing import Any

# 停用词 (英文 + 常见中文)
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might must can shall of to in on at "
    "for by with from as into through during before after above below "
    "up down out off over under again further then once here there all "
    "any both each few more most other some such no nor not only own "
    "same so than too very s t can just don should now "
    "的 了 是 在 我 你 他 她 它 们 和 与 或 但 也 都 就 这 那 "
    "一个 一种 些 么 呢 吧 啊 嗯".split()
)


def _tokenize(text: str) -> list[str]:
    """分词: 拆 camelCase, 小写, 去停用词, 轻量词干。"""
    text = text.lower()
    # 拆 camelCase (如 HawkingRadiation → hawking radiation)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # 非字母数字的都当分隔符
    raw = re.findall(r"[a-z0-9]+", text)
    # 中文按字符拆 (每个汉字一个 token)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    tokens = []
    for w in raw:
        if len(w) < 2:
            continue
        if w in _STOPWORDS:
            continue
        # 轻量词干: 去复数
        if w.endswith("s") and len(w) > 3:
            w = w[:-1]
        tokens.append(w)
    tokens.extend(cjk)
    return tokens


class BM25Index:
    """BM25 索引。会话开始时构建一次, 每轮查询。


    """

    def __init__(self) -> None:
        self._docs: list[dict[str, Any]] = []
        self._tokens: list[list[str]] = []
        self._df: dict[str, int] = {}  # document frequency
        self._avg_len: float = 0.0
        self._n: int = 0
        # BM25 参数 (Okapi 标准)
        self._k1 = 1.5
        self._b = 0.75

    def build(self, memories: list[dict[str, Any]]) -> None:
        """构建索引。"""
        self._docs = memories
        self._tokens = []
        self._df = {}
        total_len = 0
        for m in memories:
            toks = _tokenize(m.get("body", ""))
            self._tokens.append(toks)
            total_len += len(toks)
            # 更新 df
            for t in set(toks):
                self._df[t] = self._df.get(t, 0) + 1
        self._n = len(memories)
        self._avg_len = total_len / self._n if self._n > 0 else 0.0

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """BM25 搜索, 返回 top-k 结果。"""
        if self._n == 0:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        scores: list[float] = []
        for i, doc_tokens in enumerate(self._tokens):
            score = self._bm25_score(q_tokens, doc_tokens)
            scores.append((score, i))

        # 降序排列, 取 top-k
        scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i in scores[:limit]:
            if score <= 0:
                break
            doc = dict(self._docs[i])
            doc["_score"] = score
            results.append(doc)
        return results

    def _bm25_score(self, q_tokens: list[str], doc_tokens: list[str]) -> float:
        """单文档 BM25 分数。"""
        if not doc_tokens:
            return 0.0
        dl = len(doc_tokens)
        # 词频
        tf_map: dict[str, int] = {}
        for t in doc_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for qt in q_tokens:
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            df = self._df.get(qt, 0)
            if df == 0:
                continue
            # IDF (Okapi)
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            # BM25 TF
            tf_component = (tf * (self._k1 + 1)) / (
                tf + self._k1 * (1 - self._b + self._b * dl / max(self._avg_len, 1))
            )
            score += idf * tf_component
        return score


def build_index(memories: list[dict[str, Any]]) -> BM25Index:
    """构建 BM25 索引。"""
    idx = BM25Index()
    idx.build(memories)
    return idx


def recall(
    query: str,
    index: BM25Index,
    *,
    limit: int = 6,
    exclude_entities: list[str] | None = None,
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """召回: BM25 搜索 + 过滤。

    默认排除 frame 层 (frame 层不自动注入, agent 主动 read)。

    Layer A 新增参数:
    - include_types: 只保留这些 entity_type (claim/evidence/citation/tool_use/note)
    - exclude_types: 排除这些 entity_type
    """
    results = index.search(query, limit=limit * 2)  # 多取一些再过滤
    filtered = []
    for r in results:
        if exclude_entities and r.get("scope", r.get("entity")) in exclude_entities:
            continue
        if include_types and r.get("entity_type", "note") not in include_types:
            continue
        if exclude_types and r.get("entity_type", "note") in exclude_types:
            continue
        filtered.append(r)
        if len(filtered) >= limit:
            break
    return filtered


def render_recall_block(memories: list[dict[str, Any]]) -> str:
    """渲染 [Memory] 召回块, 注入到上下文。

    Layer A: 每行加 [type] 标签, 让 LLM 看到记忆的语义类型。
    """
    if not memories:
        return ""
    lines = ["[Memory]"]
    for m in memories:
        scope = m.get("scope", m.get("entity", ""))
        entity_type = m.get("entity_type", "note")
        evidence = m.get("evidence", "")
        body = m.get("body", "")
        lines.append(f"  [{scope}] [{entity_type}] [{evidence}] {body}")
    lines.append(
        "  (以上记忆来自历史会话, 可能过时 — 用 search_memory/read_memory 查询完整内容)"
    )
    return "\n".join(lines)
