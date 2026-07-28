"""compile_tex 测试: 编译成功 + 容错 (跳过崩掉的 tikzpicture 出正文 PDF)。

回归: AI 产出的 .tex 常因 TikZ 漏加载库 (如 positioning) 导致编译在第 N 行崩,
之前直接失败不出任何 PDF。容错路径应识别这类浮动环境错误, 把该环境临时
注释后重编译, 保证正文 PDF 能出来。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from axiom_core.tools.builtins.latex import _find_failing_env, compile_tex

# tectonic 可能没装 (CI/其他机器), 没装就跳过实际编译测试。
TECTONIC = shutil.which("tectonic")
requires_tectonic = pytest.mark.skipif(not TECTONIC, reason="tectonic not installed")


def _good_tex() -> str:
    """能正常编译的简单 article (无浮动环境错误)。"""
    return r"""
\documentclass{article}
\begin{document}
\title{Test}
\maketitle
\section{Intro}
Hello world body text.
\end{document}
"""


def _tikz_broken_tex() -> str:
    """tikzpicture 里用了 positioning 语法但没加载库 → 编译必崩。

    对应真实 bug: survey-complete/main.tex 第 219 行 `below left=of root`。
    """
    return r"""
\documentclass{article}
\usepackage{tikz}
\begin{document}
\title{Test}
\maketitle
\section{Intro}
Body text that must survive even if the figure below is broken.
\begin{tikzpicture}
\node (root) {A};
\node[below left=of root] (b) {B};
\end{tikzpicture}
\end{document}
"""


def test_find_failing_env_pgf_error(tmp_path: Path):
    """PGF Math Error 应推断为 tikzpicture 环境 (错误信息里没有 'tikzpicture' 字样)。"""
    tex = tmp_path / "main.tex"
    tex.write_text(_tikz_broken_tex(), encoding="utf-8")
    errors = ["! Package PGF Math Error: Unknown function `of' (in 'of root')."]
    assert _find_failing_env(tex, errors) == "tikzpicture"


def test_find_failing_env_requires_env_present(tmp_path: Path):
    """文件里没有 tikzpicture 时, 即便错误关键词匹配也不返回该环境。"""
    tex = tmp_path / "main.tex"
    tex.write_text(_good_tex(), encoding="utf-8")  # 无 tikzpicture
    errors = ["! Package PGF Math Error: Unknown function `of'."]
    assert _find_failing_env(tex, errors) is None


@requires_tectonic
@pytest.mark.asyncio
async def test_compile_success(tmp_path: Path):
    """正常 .tex 编译成功, 返回 PDF 路径在工作区。"""
    (tmp_path / "main.tex").write_text(_good_tex(), encoding="utf-8")
    r = await compile_tex(tmp_path, "main.tex")
    assert r.success, f"expected success, got: {r.message} | errors={r.errors}"
    assert r.rel_pdf == "main.pdf"
    assert r.pdf_path.exists()
    assert r.size_kb > 0


@requires_tectonic
@pytest.mark.asyncio
async def test_compile_fallback_skips_broken_tikz(tmp_path: Path):
    """崩掉的 tikzpicture 应被跳过, 正文 PDF 仍能出来 (容错核心)。"""
    (tmp_path / "main.tex").write_text(_tikz_broken_tex(), encoding="utf-8")
    r = await compile_tex(tmp_path, "main.tex")
    assert r.success, f"fallback should produce a PDF; msg={r.message}"
    assert r.rel_pdf == "main.pdf"
    assert r.pdf_path.exists()
    assert "fallback" in r.message.lower() or "skipped" in r.message.lower()
