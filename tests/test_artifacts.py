"""Artifact 版本化测试。

对照原版 0187.js _saveArtifactCommon。
测试: 三层结构 / version_of 解析 / 乐观并发 / 版本链 / marker / lineage DAG。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operon.artifacts.store import (
    ArtifactStore,
    extract_markers,
    make_marker,
)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path)


# ---------- 基本保存 ----------


def test_save_new_artifact(store):
    r = store.save(
        filename="paper.md",
        content="# hello",
        project_id="p1",
        root_frame_id="f1",
        frame_id="f1",
    )
    assert r.is_new_artifact
    assert r.version_number == 1
    assert r.stale_base is None
    assert r.artifact_id != r.version_id  # artifact 和 version 不同 id


def test_save_appends_version_by_filename(store):
    r1 = store.save(filename="paper.md", content="v1", project_id="p1", root_frame_id="f1")
    r2 = store.save(filename="paper.md", content="v2", project_id="p1", root_frame_id="f1")
    assert r1.artifact_id == r2.artifact_id  # 同 artifact
    assert r1.version_id != r2.version_id  # 不同 version
    assert r2.version_number == 2
    assert r2.stale_base is None  # 无并发冲突


def test_save_version_of_artifact_id(store):
    r1 = store.save(filename="a.txt", content="v1", project_id="p1", root_frame_id="f1")
    r2 = store.save(filename="a.txt", content="v2", project_id="p1", root_frame_id="f1", version_of=r1.artifact_id)
    assert r2.artifact_id == r1.artifact_id
    assert r2.version_number == 2


def test_save_version_of_version_id(store):
    r1 = store.save(filename="a.txt", content="v1", project_id="p1", root_frame_id="f1")
    r2 = store.save(filename="a.txt", content="v2", project_id="p1", root_frame_id="f1", version_of=r1.version_id)
    assert r2.artifact_id == r1.artifact_id
    assert r2.parent_version_id == r1.version_id


def test_version_of_invalid_raises(store):
    with pytest.raises(ValueError, match="does not match"):
        store.save(filename="a.txt", content="x", project_id="p1", root_frame_id="f1", version_of="nonexistent")


# ---------- 乐观并发 ----------


def test_optimistic_concurrency_stale_base(store):
    """基于同一 parent 追加两次 → 第二次 stale_base。"""
    r1 = store.save(filename="a.txt", content="v1", project_id="p1", root_frame_id="f1")
    # 基于 r1 追加,但中间 r2 已经更新了 latest
    r_inter = store.save(filename="a.txt", content="inter", project_id="p1", root_frame_id="f1")
    # 现在基于 r1 再追加 (r1 已不是 latest)
    r_stale = store.save(
        filename="a.txt", content="stale", project_id="p1", root_frame_id="f1", version_of=r1.version_id
    )
    assert r_stale.stale_base is not None
    assert r_stale.stale_base["based_on_version_id"] == r1.version_id
    assert r_stale.stale_base["current_latest_version_id"] == r_inter.version_id


# ---------- 读 + 版本链 ----------


def test_read_version(store):
    store.save(filename="a.txt", content="content here", project_id="p1", root_frame_id="f1")
    r2 = store.save(filename="a.txt", content="new content", project_id="p1", root_frame_id="f1")
    data = store.read(r2.version_id)
    assert data == b"new content"
    assert store.read_text(r2.version_id) == "new content"


def test_read_nonexistent(store):
    assert store.read("nonexistent") is None


def test_list_versions(store):
    store.save(filename="a.txt", content="1", project_id="p1", root_frame_id="f1")
    store.save(filename="a.txt", content="2", project_id="p1", root_frame_id="f1")
    store.save(filename="a.txt", content="3", project_id="p1", root_frame_id="f1")
    art = store.list_artifacts("p1")[0]
    versions = store.list_versions(art.id)
    assert len(versions) == 3
    assert [v.version_number for v in versions] == [1, 2, 3]


# ---------- intermediate ----------


def test_intermediate_does_not_update_latest(store):
    r1 = store.save(filename="a.txt", content="v1", project_id="p1", root_frame_id="f1")
    store.save(filename="a.txt", content="draft", project_id="p1", root_frame_id="f1", is_intermediate=True)
    # latest 应仍是 r1
    art = store.get_artifact(r1.artifact_id)
    assert art.latest_version_id == r1.version_id


# ---------- 依赖 DAG ----------


def test_dependencies_dag(store):
    r1 = store.save(filename="data.csv", content="x,y\n1,2", project_id="p1", root_frame_id="f1")
    r2 = store.save(
        filename="plot.png",
        content=b"\x89PNG binary",
        project_id="p1",
        root_frame_id="f1",
        dependencies=[{"version_id": r1.version_id, "ref_name": "input_data"}],
    )
    topo = store.get_lineage_topology(r2.version_id)
    assert any(n["version_id"] == r1.version_id for n in topo["nodes"])
    assert any(e["from"] == r2.version_id and e["to"] == r1.version_id for e in topo["edges"])


# ---------- marker ----------


def test_make_marker():
    m = make_marker("abc-123", "caption")
    assert "{{artifact:abc-123}}" in m
    assert "caption" in m


def test_extract_markers():
    text = "see ![fig1]({{artifact:vid-1}}) and ![fig2]({{artifact:vid-2}})"
    markers = extract_markers(text)
    assert len(markers) == 2
    assert markers[0] == ("fig1", "vid-1")
    assert markers[1] == ("fig2", "vid-2")


# ---------- checksum ----------


def test_checksum(store):
    r = store.save(filename="a.txt", content="hello", project_id="p1", root_frame_id="f1")
    import hashlib

    assert r.version_id  # 存了
    ver = store.get_version(r.version_id)
    assert ver.checksum == hashlib.sha256(b"hello").hexdigest()


# ---------- 不同文件名是不同 artifact ----------


def test_different_filenames_different_artifacts(store):
    r1 = store.save(filename="a.txt", content="x", project_id="p1", root_frame_id="f1")
    r2 = store.save(filename="b.txt", content="x", project_id="p1", root_frame_id="f1")
    assert r1.artifact_id != r2.artifact_id
