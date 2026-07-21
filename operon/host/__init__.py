"""Host 对象 — 给 Python kernel 的进程内接口。



原版用 stdio JSON-RPC 桥接 TS 主进程和 Python kernel。
Python 全栈下,host 是进程内对象,直接调用 operon 内部组件 (ArtifactStore/LLMClient 等),
注入到 python 工具的 exec namespace,让 agent 写的 python 代码能调:
    host.llm("总结这段") → 调 LLM
    host.lineage[vid]    → 取 lineage
    host.artifact_path(vid) → 版本文件路径
    host.current_model() → 当前模型名
    host.query(sql)      → 只读查询 (简化)

简化: 跳过 delegate/collect/mcp (ultra_mode/repl 专用)。
保留 analysis kernel 的核心: llm/lineage/artifact_path/current_model/artifacts。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from operon.artifacts.store import ArtifactStore
    from operon.llm.base import LLMClient


class HostLineageAccessor:
    """host.lineage 访问器。对照原版 _LineageAccessor (0221.js:1795)。

    host.lineage[vid] → 取 lineage
    host.lineage.graph(vid) → 取 DAG 拓扑
    """

    def __init__(self, artifact_store: ArtifactStore | None):
        self._store = artifact_store
        self._cache: dict[str, dict] = {}

    def __getitem__(self, version_id: str) -> dict:
        vid = version_id.lower()
        if vid in self._cache:
            return self._cache[vid]
        if self._store is None:
            raise RuntimeError("host.lineage unavailable: artifact store not configured")
        lineage = self._store.get_lineage(vid)
        if not lineage:
            raise KeyError(f"no lineage for version_id '{version_id}'")
        self._cache[vid] = lineage
        return lineage

    def __contains__(self, version_id: str) -> bool:
        try:
            self[version_id]
            return True
        except KeyError:
            return False

    def graph(self, version_id: str, direction: str = "up") -> dict:
        """DAG 拓扑。对照原版 get_lineage_topology。"""
        if self._store is None:
            raise RuntimeError("host.lineage.graph unavailable")
        return self._store.get_lineage_topology(version_id.lower())


class HostQueryAccessor:
    """host.query 访问器。对照原版 _QueryAccessor (0221.js:1893)。

    host.query(sql, params, limit) → 只读查询
    host.query.schema() → 表结构
    简化: 不实现完整 SQL 重写 (项目作用域过滤),仅支持 ArtifactStore 内存查询。
    """

    def __init__(self, artifact_store: ArtifactStore | None):
        self._store = artifact_store

    def __call__(self, sql: str, params: list | None = None, limit: int = 200) -> dict:
        """只读查询。简化: 支持查 artifacts/versions 的元数据。"""
        if self._store is None:
            raise RuntimeError("host.query unavailable: artifact store not configured")
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN")):
            raise ValueError("host.query only supports SELECT/WITH/PRAGMA/EXPLAIN")
        # 简化: 只支持预定义查询
        sql_u = sql.upper()
        if "FROM ARTIFACTS" in sql_u or "FROM ARTIFACT_VERSIONS" in sql_u:
            return self._query_artifacts(sql, limit)
        raise ValueError(
            "host.query supports: SELECT FROM artifacts / SELECT FROM artifact_versions"
        )

    def schema(self) -> dict:
        """返回可查询表结构。对照原版 query_schema (N6O)。"""
        return {
            "artifacts": ["id", "filename", "project_id", "latest_version_id", "frame_id"],
            "artifact_versions": [
                "id", "artifact_id", "version_number", "content_type",
                "size_bytes", "checksum", "agent_name", "language", "parent_version_id",
            ],
        }

    def _query_artifacts(self, sql: str, limit: int) -> dict:
        """查询 artifacts (简化)。"""
        arts = self._store.list_artifacts() if self._store else []
        rows = []
        for a in arts[:limit]:
            if "artifact_versions" in sql.upper():
                for v in self._store.list_versions(a.id):
                    rows.append([
                        v.id, v.artifact_id, v.version_number, v.content_type,
                        v.size_bytes, v.checksum, v.agent_name, v.language, v.parent_version_id,
                    ])
            else:
                rows.append([
                    a.id, a.filename, a.project_id, a.latest_version_id, a.frame_id,
                ])
        cols = (
            [
                "id", "artifact_id", "version_number", "content_type", "size_bytes",
                "checksum", "agent_name", "language", "parent_version_id",
            ]
            if "artifact_versions" in sql.upper()
            else ["id", "filename", "project_id", "latest_version_id", "frame_id"]
        )
        return {
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }


class Host:
    """host 对象 (进程内)。对照原版 _OperonSDK (0221.js:2716)。

    注入到 python 工具的 exec namespace,agent 写的 python 代码可直接调。
    sys.modules["host"] 注册使 import host 也能工作。
    """

    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        artifact_store: ArtifactStore | None = None,
        model: str | None = None,
        mcp_manager: Any = None,
    ):
        self._llm = llm
        self._store = artifact_store
        self._model = model
        self._mcp_manager = mcp_manager
        self.lineage = HostLineageAccessor(artifact_store)
        self.query = HostQueryAccessor(artifact_store)

    async def mcp(self, server: str, tool: str, **kwargs) -> str:
        """显式调用 MCP 工具。对照原版 host.mcp (0814.js:842, repl-only)。

        原版仅在 repl kernel 暴露。本项目在 python kernel 直接可用:
            host.mcp("web_search_prime", "web_search_prime", search="...")
        双轨: 也可直接调 mcp__server__tool 工具。
        """
        if self._mcp_manager is None:
            raise RuntimeError("host.mcp unavailable: no MCP manager configured")
        return await self._mcp_manager.call_tool(server, tool, kwargs)

    async def llm(
        self,
        request: str | dict | list | None = None,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        **options,
    ) -> dict | list:
        """单次/批量 LLM 调用。对照原版 host.llm (0221.js:2161)。

        - str/dict → 单次,返回 {text, model, usage, stop_reason}
        - list → 批量,返回 [{...}] (位置对齐)
        """
        import asyncio

        from operon.llm.messages import Message, Role

        if self._llm is None:
            raise RuntimeError("host.llm unavailable: LLM client not configured")

        async def _single(req: str | dict) -> dict:
            use_model = model
            use_max = max_tokens
            use_system = system
            if isinstance(req, str):
                msgs = [Message(role=Role.USER, content=req)]
            elif isinstance(req, dict):
                prompt = req.get("prompt") or req.get("messages")
                if isinstance(prompt, str):
                    msgs = [Message(role=Role.USER, content=prompt)]
                elif isinstance(prompt, list):
                    msgs = prompt
                else:
                    msgs = [Message(role=Role.USER, content=str(prompt))]
                use_system = req.get("system", system)
                use_model = req.get("model", model)
                use_max = req.get("max_tokens", max_tokens)
            else:
                raise TypeError(f"host.llm 不支持的请求类型: {type(req)}")
            try:
                resp = await self._llm.chat(
                    msgs, system=use_system, model=use_model or self._model, max_tokens=use_max
                )
                text = "".join(b.text for b in resp.content if hasattr(b, "text"))
                return {
                    "text": text,
                    "model": resp.model,
                    "usage": {
                        "input_tokens": resp.usage.input_tokens,
                        "output_tokens": resp.usage.output_tokens,
                    },
                    "stop_reason": resp.stop_reason.value,
                }
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}

        if isinstance(request, list):
            # 批量并发
            results = await asyncio.gather(*[_single(r) for r in request])
            return list(results)
        return await _single(request if request is not None else (options.get("prompt", "")))

    def artifact_path(self, version_id: str) -> str:
        """version_id → 工作区文件路径。对照原版 host.artifact_path (0221.js:3425)。"""
        if not isinstance(version_id, str) or not version_id:
            raise TypeError("artifact_path(version_id) requires a non-empty string")
        if self._store is None:
            raise RuntimeError("host.artifact_path unavailable")
        ver = self._store.get_version(version_id)
        if ver is None:
            raise KeyError(f"version '{version_id}' not found")
        return str(self._store.workspace / ver.storage_path)

    def artifacts(
        self,
        *,
        version_id: str | None = None,
        filename: str | None = None,
        limit: int = 200,
    ) -> dict:
        """列出/查找 artifact。对照原版 host.artifacts (0221.js:3202)。"""
        if self._store is None:
            raise RuntimeError("host.artifacts unavailable")
        if version_id:
            ver = self._store.get_version(version_id)
            if ver is None:
                return {"count": 0, "artifacts": []}
            art = self._store.get_artifact(ver.artifact_id)
            return {
                "count": 1,
                "artifacts": [
                    {
                        "id": art.id if art else ver.artifact_id,
                        "filename": art.filename if art else "",
                        "latest_version_id": ver.id,
                        "content_type": ver.content_type,
                        "size_bytes": ver.size_bytes,
                    }
                ],
            }
        arts = self._store.list_artifacts()
        if filename:
            arts = [a for a in arts if filename.lower() in a.filename.lower()]
        items = []
        for a in arts[:limit]:
            items.append(
                {
                    "id": a.id,
                    "filename": a.filename,
                    "latest_version_id": a.latest_version_id,
                    "project_id": a.project_id,
                }
            )
        return {"count": len(items), "artifacts": items}

    def current_model(self) -> str:
        """当前会话模型。对照原版 host.current_model (0221.js:2485)。"""
        return self._model or "unknown"

    def reasoning_model(self) -> str:
        """推理模型 (简化: 同 current_model)。"""
        return self._model or "unknown"

    async def list_models(self) -> list[str]:
        """可见模型列表 (简化: 只当前)。"""
        return [self._model] if self._model else []


def make_host(
    *,
    llm: LLMClient | None = None,
    artifact_store: ArtifactStore | None = None,
    model: str | None = None,
    mcp_manager: Any = None,
) -> Host:
    """创建 host 对象。

    对照原版 0221.js:2716。原版在独立 kernel 进程注册 sys.modules,
    本项目在主进程运行,不能污染 sys.modules (会让 operon 包 import 失败)。
    host 仅注入 python 工具的 exec namespace (agent 代码里 `host.xxx` 可用)。
    """
    return Host(llm=llm, artifact_store=artifact_store, model=model, mcp_manager=mcp_manager)
