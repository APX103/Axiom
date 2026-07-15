"""Provenance — 运行时溯源插装。

对应原版: 0221.js "runtime provenance instrumentation — CORE (1/3)" + WRAPS (2/3)。

原版 provenance 把 agent 的每次 host 调用、artifact 产出、代码执行关联起来,
形成可复现的 lineage 图。环境开关 OPERON_PROVENANCE_OFF=1 可关。
刻意拆成三片 "so the sec-scanner can review it"。

简化版 (保留核心):
- ProvenanceRecorder: 记录 host 调用 + artifact 产出 (execution_log)
- auto_extract: 从 cell 代码 + 依赖的 artifact 提取 lineage
- OPERON_PROVENANCE_OFF 环境开关

不做: guardProvenanceShim (AST 插装) / leaf-wrap recall lane / 复杂的 3 片拆分。
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def provenance_enabled() -> bool:
    """是否启用 provenance。对照原版 OPERON_PROVENANCE_OFF 环境开关。"""
    return os.environ.get("OPERON_PROVENANCE_OFF", "0") != "1"


@dataclass
class ExecutionRecord:
    """一次 host 调用 / 代码执行的记录。对照原版 execution_log 表。"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cell_id: str | None = None  # 产生它的 cell
    method: str = ""  # host 方法名 (llm/query/save_artifacts 等)
    args_summary: str = ""  # 参数摘要 (不存敏感值)
    result_summary: str = ""  # 结果摘要
    input_version_ids: list[str] = field(default_factory=list)  # 消费的 artifact
    output_version_id: str | None = None  # 产出的 artifact
    frame_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0


class ProvenanceRecorder:
    """Provenance 记录器。对照原版 provenance runtime instrumentation。

    记录每次 host 调用 + artifact 产出,供 lineage 复现。
    挂在 host 对象上,wrap 关键方法。
    """

    def __init__(self):
        self.enabled = provenance_enabled()
        self._log: list[ExecutionRecord] = []
        self._cell_counter = 0

    def record(
        self,
        *,
        method: str,
        cell_id: str | None = None,
        args_summary: str = "",
        result_summary: str = "",
        input_version_ids: list[str] | None = None,
        output_version_id: str | None = None,
        frame_id: str | None = None,
        duration_ms: float = 0.0,
    ) -> ExecutionRecord | None:
        """记录一次执行。返回记录 (或 None 若禁用)。"""
        if not self.enabled:
            return None
        rec = ExecutionRecord(
            cell_id=cell_id,
            method=method,
            args_summary=args_summary[:500],  # 摘要,不存全量
            result_summary=result_summary[:500],
            input_version_ids=list(input_version_ids or []),
            output_version_id=output_version_id,
            frame_id=frame_id,
            duration_ms=duration_ms,
        )
        self._log.append(rec)
        return rec

    def new_cell_id(self) -> str:
        """生成新的 cell id。"""
        self._cell_counter += 1
        return f"cell_{self._cell_counter}"

    def get_log(self) -> list[ExecutionRecord]:
        """获取完整执行日志。"""
        return list(self._log)

    def lineage_for(self, version_id: str) -> list[ExecutionRecord]:
        """产出某个 artifact 的所有相关执行记录。"""
        related = []
        for rec in self._log:
            if rec.output_version_id == version_id or version_id in rec.input_version_ids:
                related.append(rec)
        return related

    def export(self) -> list[dict[str, Any]]:
        """导出执行日志 (JSON 可序列化)。供复现。"""
        return [
            {
                "id": r.id,
                "cell_id": r.cell_id,
                "method": r.method,
                "args_summary": r.args_summary,
                "result_summary": r.result_summary,
                "input_version_ids": r.input_version_ids,
                "output_version_id": r.output_version_id,
                "frame_id": r.frame_id,
                "timestamp": r.timestamp,
                "duration_ms": r.duration_ms,
            }
            for r in self._log
        ]


def auto_extract_lineage(code: str, artifact_store: Any | None = None) -> dict[str, Any]:
    """从代码自动提取 lineage。

    对照原版 auto_extract。分析代码里引用的 artifact ({{artifact:VID}} 或 host.artifact_path)
    和产出的 save_artifacts,推断依赖关系。
    """
    from operon.artifacts.store import extract_markers  # 延迟导入

    lineage: dict[str, Any] = {
        "code": code,
        "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "input_artifacts": [],
        "output_artifacts": [],
    }

    # 提取 {{artifact:VID}} 标记 (输入依赖)
    markers = extract_markers(code)
    lineage["input_artifacts"] = [{"version_id": vid, "caption": caption} for caption, vid in markers]

    # 提取 host.artifact_path("vid") 调用
    import re

    path_calls = re.findall(r'artifact_path\(\s*["\']([0-9a-fA-F-]+)["\']', code)
    for vid in path_calls:
        if vid not in [m["version_id"] for m in lineage["input_artifacts"]]:
            lineage["input_artifacts"].append({"version_id": vid, "caption": "via artifact_path"})

    return lineage


# 全局 recorder (单例,供 host 对象使用)
_global_recorder: ProvenanceRecorder | None = None


def get_recorder() -> ProvenanceRecorder:
    """获取全局 provenance recorder。"""
    global _global_recorder
    if _global_recorder is None:
        _global_recorder = ProvenanceRecorder()
    return _global_recorder
