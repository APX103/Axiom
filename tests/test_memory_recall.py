"""memory recall 单元测试 (Layer A)。

覆盖:
- BM25 索引构建 (带新字段不破坏)
- recall 的 include_types / exclude_types 过滤
- exclude_entities 同时兼容 scope 和 entity 字段
- render_recall_block 输出含 [scope] [entity_type] [evidence] 标签
- 空索引 / 空 query 不抛异常
"""

from __future__ import annotations

from operon.memory.recall import BM25Index, build_index, recall, render_recall_block


def _make_memory(
    body: str,
    *,
    scope: str = "project",
    entity_type: str = "note",
    evidence: str = "stated",
    entity: str | None = None,
    mem_id: str | None = None,
) -> dict:
    """构造一个测试用 memory dict (模拟 _row_to_dict 输出)。"""
    return {
        "id": mem_id or f"mem_{hash(body) & 0xFFFFFF:06x}",
        "entity": entity or scope,  # 老字段
        "scope": scope,
        "entity_type": entity_type,
        "body": body,
        "evidence": evidence,
    }


# ---------- BM25Index 基础 ----------


def test_bm25_build_and_search():
    """索引构建 + 基本搜索。"""
    mems = [
        _make_memory("kinase 142 is catalytic residue", entity_type="claim"),
        _make_memory("Smith 2023 kinase paper", entity_type="citation"),
        _make_memory("user prefers Chinese", scope="profile"),
    ]
    idx = build_index(mems)
    results = idx.search("kinase", limit=3)
    assert len(results) >= 1
    # kinase 相关的排在前面
    assert "kinase" in results[0]["body"].lower()


def test_bm25_empty_index():
    """空索引不抛异常, 返回空。"""
    idx = BM25Index()
    idx.build([])
    assert idx.search("anything") == []


def test_bm25_empty_query():
    """空 query (无 token) 返回空。"""
    idx = build_index([_make_memory("some memory")])
    # 纯停用词 / 单字符
    assert idx.search("the") == []
    assert idx.search("a") == []


# ---------- recall 过滤 ----------


def test_recall_exclude_entities_frame():
    """默认 exclude_entities=['frame'] 不返回 frame 层。"""
    mems = [
        _make_memory("project fact", scope="project"),
        _make_memory("frame note", scope="frame"),
    ]
    idx = build_index(mems)
    results = recall("fact", idx, exclude_entities=["frame"])
    assert all(r["scope"] != "frame" for r in results)


def test_recall_include_types():
    """include_types 只保留指定 entity_type。"""
    mems = [
        _make_memory("kinase claim", entity_type="claim"),
        _make_memory("kinase evidence", entity_type="evidence"),
        _make_memory("kinase note", entity_type="note"),
    ]
    idx = build_index(mems)
    results = recall("kinase", idx, include_types=["claim"])
    assert len(results) >= 1
    assert all(r["entity_type"] == "claim" for r in results)


def test_recall_exclude_types():
    """exclude_types 排除指定 entity_type。"""
    mems = [
        _make_memory("kinase claim", entity_type="claim"),
        _make_memory("kinase evidence", entity_type="evidence"),
    ]
    idx = build_index(mems)
    results = recall("kinase", idx, exclude_types=["evidence"])
    assert all(r["entity_type"] != "evidence" for r in results)


def test_recall_include_and_exclude_types_combined():
    """include + exclude 组合。"""
    mems = [
        _make_memory("kinase claim", entity_type="claim"),
        _make_memory("kinase evidence", entity_type="evidence"),
        _make_memory("kinase citation", entity_type="citation"),
    ]
    idx = build_index(mems)
    # 只要 claim 和 citation, 不要 evidence
    results = recall(
        "kinase", idx,
        include_types=["claim", "citation"],
        exclude_types=["evidence"],
    )
    types = {r["entity_type"] for r in results}
    assert "evidence" not in types
    assert types.issubset({"claim", "citation"})


def test_recall_limit():
    """limit 限制返回数量。"""
    mems = [_make_memory(f"fact number {i}") for i in range(20)]
    idx = build_index(mems)
    results = recall("fact", idx, limit=5)
    assert len(results) <= 5


def test_recall_compatible_with_legacy_entity_field():
    """recall 的 exclude_entities 同时检查 scope 和 entity (向后兼容)。

    老数据可能 scope=NULL (未迁移), 只有 entity 字段; recall 仍能正确过滤。
    """
    mems = [
        {"id": "m1", "entity": "project", "body": "project fact"},  # 无 scope (老格式)
        {"id": "m2", "entity": "frame", "body": "frame note", "scope": "frame"},
    ]
    idx = build_index(mems)
    results = recall("fact", idx, exclude_entities=["frame"])
    # frame 被排除 (无论通过 scope 还是 entity 检测)
    assert all(r.get("scope", r.get("entity")) != "frame" for r in results)


# ---------- render_recall_block ----------


def test_render_recall_block_includes_entity_type():
    """渲染块每行含 [scope] [entity_type] [evidence] 标签。"""
    mems = [
        _make_memory(
            "kinase 142 is catalytic",
            scope="project", entity_type="claim", evidence="observed",
        ),
        _make_memory(
            "Smith 2023 paper",
            scope="project", entity_type="citation", evidence="stated",
        ),
    ]
    block = render_recall_block(mems)
    assert "[Memory]" in block
    assert "[project]" in block
    assert "[claim]" in block
    assert "[citation]" in block
    assert "[observed]" in block
    assert "kinase 142 is catalytic" in block


def test_render_recall_block_empty_returns_empty_string():
    """空列表返回空字符串。"""
    assert render_recall_block([]) == ""


def test_render_recall_block_legacy_fallback():
    """老格式 (无 scope/entity_type) 渲染不抛异常, 用默认值。"""
    mems = [{"id": "m1", "entity": "project", "body": "old fact", "evidence": "stated"}]
    block = render_recall_block(mems)
    assert "old fact" in block
    # entity_type 缺失时默认 note
    assert "[note]" in block
