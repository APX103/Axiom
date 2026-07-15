"""Skill 词法检索: BM25 + Jaccard + RRF。

对应原版: 0771.js TS 类 (BM25+Jaccard+RRF) + 0772.js (词汇表) + 0808.js:206 search_skills。

算法:
1. tokenize (拆词 + camelCase + 去停用词 + 去复数)
2. BM25 (k1=1.2, b=0.75) + Jaccard 两路打分
3. RRF 融合: rrf = 1/(60+bm25_rank) + 1/(60+jaccard_rank) (加法偏置, 对照 akz=60)
4. 阈值 0.029 过滤 (对照 search_rrf_threshold)
5. top-N
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .parser import Skill

# 常数 (对照原版 0771.js:451-464)
BM25_K1 = 1.2  # nkz
BM25_B = 0.75  # skz
RRF_K = 60  # akz (加法偏置, 非标准 RRF)
RRF_THRESHOLD = 0.029  # search_rrf_threshold
BODY_SLICE = 2000  # TC_

# 停用词 (对照原版 _Jz, 0772.js:30-90, 取常见)
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "is", "are",
        "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "can", "this", "that", "these",
        "those", "i", "you", "he", "she", "it", "we", "they", "what", "which", "who",
        "how", "where", "when", "why", "run", "use", "using", "get", "set", "with",
        "from", "by", "as", "into", "about", "my", "your", "no", "yes", "not",
    }
)

# 缩写扩展 (对照原版 mpw, 0772.js:11-29, 取常见)
_ABBREVS = {
    "scrna": "single cell rna sequencing",
    "de": "differential expression",
    "pca": "principal component analysis",
    "tsne": "t-distributed stochastic neighbor embedding",
    "umi": "unique molecular identifier",
    "adata": "anndata",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "cv": "computer vision",
}


def tokenize(text: str) -> list[str]:
    """分词。对照原版 SxO (0771.js:82-94)。

    拆非字母数字 → 拆 camelCase → 小写 → 去停用词 → 去复数。
    """
    # 拆 camelCase / PascalCase
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # 拆非字母数字
    raw = re.split(r"[^a-zA-Z0-9]+", text)
    tokens = []
    for w in raw:
        w = w.lower().strip()
        if len(w) < 2:
            continue
        if w in _STOPWORDS:
            continue
        # 去复数: len>3 且以 s 结尾且非 ss
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in _STOPWORDS:
            tokens.append(w)
    return tokens


def expand_query(text: str) -> str:
    """缩写扩展。对照原版 cpw (0771.js:54-59)。

    命中缩写就在原词后追加展开形式。
    """
    def repl(m: re.Match) -> str:
        word = m.group(0).lower()
        s = m.group(1) if m.group(1) else ""
        if word in _ABBREVS:
            return f"{word}{s} {_ABBREVS[word]}"
        return m.group(0)

    return re.sub(r"\b([a-zA-Z]{2,10})(s?)\b", repl, text)


@dataclass
class SearchResult:
    """检索结果。"""

    skill: Skill
    score: float  # RRF 融合分
    bm25: float
    jaccard: float


class SkillIndex:
    """Skill 倒排索引。对照原版 TS (0771.js:95-208)。"""

    def __init__(self, skills: list[Skill]):
        self.skills = skills
        self._docs: list[list[str]] = []  # 每个 skill 的 token 列表
        self._df: dict[str, int] = {}  # document frequency
        self._avg_len = 0.0
        self._build()

    def _build(self) -> None:
        doc_tokens: list[list[str]] = []
        for s in self.skills:
            # 索引文本: name + index_text (若空则用 description + body)
            idx_text = s.index_text or f"{s.description} {s.body}"
            tokens = tokenize(f"{s.name} {idx_text}")
            doc_tokens.append(tokens)
        self._docs = doc_tokens
        # DF
        df: dict[str, int] = {}
        for tokens in doc_tokens:
            for t in set(tokens):
                df[t] = df.get(t, 0) + 1
        self._df = df
        self._avg_len = sum(len(t) for t in doc_tokens) / max(1, len(doc_tokens))

    def _bm25_score(self, query_tokens: list[str], doc_idx: int) -> float:
        """BM25 打分。对照原版 (0771.js:132-146)。"""
        doc = self._docs[doc_idx]
        doc_len = len(doc)
        # 词频
        tf: dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        N = len(self._docs)
        score = 0.0
        for term in set(query_tokens):  # 去重
            if term not in tf:
                continue
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            f = tf[term]
            score += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / max(1, self._avg_len)))
        return score

    def _jaccard_score(self, query_tokens: list[str], doc_idx: int) -> float:
        """Jaccard 相似度。对照原版 (0771.js:147-153)。"""
        qset = set(query_tokens)
        dset = set(self._docs[doc_idx])
        if not qset or not dset:
            return 0.0
        inter = len(qset & dset)
        return inter / (len(qset) + len(dset) - inter)

    def search(self, query: str, *, max_results: int = 4) -> list[SearchResult]:
        """检索。对照原版 AxO (0771.js:375-423)。

        BM25 + Jaccard 两路 → RRF 融合 → 阈值过滤 → top-N。
        """
        if not self.skills:
            return []
        expanded = expand_query(query)
        query_tokens = tokenize(expanded)
        if not query_tokens:
            return []

        # 两路打分
        bm25_scores = [(i, self._bm25_score(query_tokens, i)) for i in range(len(self.skills))]
        jac_scores = [(i, self._jaccard_score(query_tokens, i)) for i in range(len(self.skills))]

        # 排序取 rank (从 1)
        bm25_sorted = sorted(bm25_scores, key=lambda x: -x[1])
        jac_sorted = sorted(jac_scores, key=lambda x: -x[1])
        bm25_rank = {idx: r + 1 for r, (idx, _) in enumerate(bm25_sorted)}
        jac_rank = {idx: r + 1 for r, (idx, _) in enumerate(jac_sorted)}

        # RRF 融合 (加法偏置, 对照 akz=60)
        results: list[SearchResult] = []
        for i, s in enumerate(self.skills):
            b_score = bm25_scores[i][1]
            j_score = jac_scores[i][1]
            rrf = 0.0
            if b_score > 0:
                rrf += 1.0 / (RRF_K + bm25_rank[i])
            if j_score > 0:
                rrf += 1.0 / (RRF_K + jac_rank[i])
            if rrf >= RRF_THRESHOLD:
                results.append(SearchResult(skill=s, score=rrf, bm25=b_score, jaccard=j_score))

        results.sort(key=lambda x: -x.score)
        return results[:max_results]


def search_skills(
    skills: list[Skill], query: str, *, max_results: int = 4
) -> list[SearchResult]:
    """便捷入口。对照原版 search_skills 工具 (0808.js:206)。"""
    index = SkillIndex(skills)
    return index.search(query, max_results=max_results)
