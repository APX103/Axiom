"""host 对象测试。

对照原版 0221.js SDK + 0814.js dispatcher。
测试: lineage/artifact_path/current_model/artifacts/query + llm (mock)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from axiom_core.artifacts.store import ArtifactStore
from axiom_core.host import Host, make_host
from axiom_core.llm.base import LLMClient
from axiom_core.llm.messages import LLMResponse, StopReason, TextBlock, TokenUsage


class MockLLM(LLMClient):
    async def chat(
        self, messages, *, system=None, tools=None, model=None, max_tokens=8192,
        temperature=None, **kw
    ):
        return LLMResponse(
            content=[TextBlock(text="mock response")],
            stop_reason=StopReason.END_TURN,
            model=model or "mock-model",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    def count_tokens(self, text):
        return len(text) // 4

    async def close(self):
        pass


@pytest.fixture
def store_with_artifact(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    r = store.save(filename="data.csv", content="x,y\n1,2", project_id="p1", root_frame_id="f1")
    return store, r


# ---------- current_model ----------


def test_current_model(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(llm=MockLLM(), artifact_store=store, model="step-3.7-flash")
    assert host.current_model() == "step-3.7-flash"


def test_current_model_default():
    host = Host()
    assert host.current_model() == "unknown"


# ---------- artifact_path ----------


def test_artifact_path(store_with_artifact, tmp_path):
    store, r = store_with_artifact
    host = Host(artifact_store=store, model="m")
    path = host.artifact_path(r.version_id)
    assert "data.csv" in path or "v" in path
    assert (tmp_path / Path(path).relative_to(tmp_path)).exists() or Path(path).exists()


def test_artifact_path_not_found(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    with pytest.raises(KeyError):
        host.artifact_path("nonexistent")


def test_artifact_path_empty_raises():
    host = Host()
    with pytest.raises(TypeError):
        host.artifact_path("")


# ---------- lineage ----------


def test_lineage_getitem(store_with_artifact):
    store, r = store_with_artifact
    host = Host(artifact_store=store, model="m")
    lineage = host.lineage[r.version_id]
    assert lineage["version_id"] == r.version_id
    assert lineage["artifact_id"] == r.artifact_id


def test_lineage_not_found(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    with pytest.raises(KeyError):
        _ = host.lineage["nonexistent"]


def test_lineage_contains(store_with_artifact):
    store, r = store_with_artifact
    host = Host(artifact_store=store, model="m")
    assert r.version_id in host.lineage
    assert "nonexistent" not in host.lineage


def test_lineage_graph(store_with_artifact):
    store, r = store_with_artifact
    # 加依赖
    r2 = store.save(
        filename="plot.png", content=b"png", project_id="p1", root_frame_id="f1",
        dependencies=[{"version_id": r.version_id, "ref_name": "input"}],
    )
    host = Host(artifact_store=store, model="m")
    topo = host.lineage.graph(r2.version_id)
    assert topo["root_version_id"] == r2.version_id
    assert any(n["version_id"] == r.version_id for n in topo["nodes"])


# ---------- artifacts ----------


def test_artifacts_list(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    result = host.artifacts()
    assert result["count"] >= 1
    assert any(a["filename"] == "data.csv" for a in result["artifacts"])


def test_artifacts_by_version(store_with_artifact):
    store, r = store_with_artifact
    host = Host(artifact_store=store, model="m")
    result = host.artifacts(version_id=r.version_id)
    assert result["count"] == 1
    assert result["artifacts"][0]["filename"] == "data.csv"


def test_artifacts_filename_filter(store_with_artifact):
    store, _ = store_with_artifact
    store.save(filename="other.txt", content="x", project_id="p1", root_frame_id="f1")
    host = Host(artifact_store=store, model="m")
    result = host.artifacts(filename="data")
    assert all("data" in a["filename"].lower() for a in result["artifacts"])


# ---------- query ----------


def test_query_select(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    result = host.query("SELECT * FROM artifacts")
    assert "columns" in result
    assert result["row_count"] >= 1


def test_query_rejects_non_select(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    with pytest.raises(ValueError, match="SELECT"):
        host.query("DROP TABLE artifacts")


def test_query_schema(store_with_artifact):
    store, _ = store_with_artifact
    host = Host(artifact_store=store, model="m")
    schema = host.query.schema()
    assert "artifacts" in schema
    assert "artifact_versions" in schema


# ---------- llm ----------


@pytest.mark.asyncio
async def test_host_llm_single():
    host = Host(llm=MockLLM(), model="m")
    result = await host.llm("hello")
    assert result["text"] == "mock response"
    assert result["model"] == "m"
    assert "input_tokens" in result["usage"]


@pytest.mark.asyncio
async def test_host_llm_batch():
    host = Host(llm=MockLLM(), model="m")
    result = await host.llm(["q1", "q2", "q3"])
    assert isinstance(result, list)
    assert len(result) == 3
    assert all(r["text"] == "mock response" for r in result)


@pytest.mark.asyncio
async def test_host_llm_dict_form():
    host = Host(llm=MockLLM(), model="m")
    result = await host.llm({"prompt": "test", "max_tokens": 100})
    assert result["text"] == "mock response"


# ---------- make_host 工厂 ----------


def test_make_host_creates_host(store_with_artifact):
    store, _ = store_with_artifact
    host = make_host(llm=MockLLM(), artifact_store=store, model="m")
    assert host.current_model() == "m"
    assert host.lineage is not None
    assert host.query is not None


def test_host_injected_to_python_namespace(tmp_path):
    """host 注入 exec namespace 后,agent 代码能用 host.current_model()。"""
    import sys

    # 清理可能的污染
    sys.modules.pop("host", None)
    sys.modules.pop("axiom_core", None)

    from axiom_core.frames.service import FrameService
    from axiom_core.tools.builtins import exec as exec_mod
    from axiom_core.tools.context import ToolContext

    svc = FrameService()
    frame = svc.create_root_frame()
    ctx = ToolContext(frame=frame, frame_service=svc, workspace=tmp_path)
    ctx.host = make_host(model="test-model")

    # 注入到 _PYTHON_NS (模拟 exec.py 的行为)
    exec_mod._PYTHON_NS["host"] = ctx.host
    result = asyncio.run(exec_mod.python(ctx, "print(host.current_model())"))
    assert "test-model" in result
    # 清理
    exec_mod._PYTHON_NS.pop("host", None)
