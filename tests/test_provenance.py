"""Provenance 测试。

对照原版 0221.js provenance instrumentation。
测试: 记录 / lineage_for / auto_extract / 环境开关。
"""

from __future__ import annotations

from operon.artifacts.store import make_marker
from operon.citations.provenance import (
    ProvenanceRecorder,
    auto_extract_lineage,
    get_recorder,
    provenance_enabled,
)


def test_enabled_by_default():
    assert provenance_enabled() is True


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("OPERON_PROVENANCE_OFF", "1")
    assert provenance_enabled() is False


def test_record():
    rec = ProvenanceRecorder()
    r = rec.record(method="save_artifacts", args_summary="file=x.csv", output_version_id="v1")
    assert r is not None
    assert r.method == "save_artifacts"
    assert r.output_version_id == "v1"
    assert len(rec.get_log()) == 1


def test_record_disabled():
    rec = ProvenanceRecorder()
    rec.enabled = False
    r = rec.record(method="llm")
    assert r is None
    assert rec.get_log() == []


def test_record_truncates_long_summary():
    rec = ProvenanceRecorder()
    long_args = "x" * 1000
    r = rec.record(method="test", args_summary=long_args)
    assert len(r.args_summary) == 500


def test_lineage_for():
    rec = ProvenanceRecorder()
    rec.record(method="save", output_version_id="v_out", input_version_ids=["v_in1", "v_in2"])
    rec.record(method="llm", output_version_id="v_other")
    # v_out 的 lineage
    related = rec.lineage_for("v_out")
    assert len(related) == 1
    assert related[0].output_version_id == "v_out"
    # v_in1 作为输入
    related = rec.lineage_for("v_in1")
    assert len(related) == 1


def test_export():
    rec = ProvenanceRecorder()
    rec.record(method="m1", output_version_id="v1")
    exported = rec.export()
    assert len(exported) == 1
    assert exported[0]["method"] == "m1"
    assert exported[0]["output_version_id"] == "v1"


def test_new_cell_id():
    rec = ProvenanceRecorder()
    c1 = rec.new_cell_id()
    c2 = rec.new_cell_id()
    assert c1 == "cell_1"
    assert c2 == "cell_2"


def test_get_recorder_singleton():
    r1 = get_recorder()
    r2 = get_recorder()
    assert r1 is r2


# ---------- auto_extract ----------


def test_auto_extract_markers():
    code = f"""
import matplotlib.pyplot as plt
img = plt.imread("{make_marker('vid-1', 'input data')}")
plt.savefig("output.png")
"""
    lineage = auto_extract_lineage(code)
    assert lineage["code_hash"]
    assert any(a["version_id"] == "vid-1" for a in lineage["input_artifacts"])


def test_auto_extract_artifact_path():
    code = 'path = host.artifact_path("abc-123-def")'
    lineage = auto_extract_lineage(code)
    assert any(a["version_id"] == "abc-123-def" for a in lineage["input_artifacts"])


def test_auto_extract_no_artifacts():
    code = "x = 1 + 2\nprint(x)"
    lineage = auto_extract_lineage(code)
    assert lineage["input_artifacts"] == []
    assert lineage["output_artifacts"] == []


def test_auto_extract_code_hash_stable():
    code = "print('hello')"
    l1 = auto_extract_lineage(code)
    l2 = auto_extract_lineage(code)
    assert l1["code_hash"] == l2["code_hash"]
