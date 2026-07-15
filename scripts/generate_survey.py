"""Generate a complete survey via the local Axiom backend API (no MCP).

Usage:
    uv run python scripts/generate_survey.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
WORKSPACE = Path("/Volumes/ssd/main_link/.axiom/survey-complete")

PROMPT = """Write a comprehensive, publication-quality survey paper on the topic:

"Chain-of-Thought Prompting and Reasoning in Large Language Models: Methods, Theory, and Applications"

The survey must be written directly to the workspace as a complete LaTeX document:
- main.tex: a full, self-contained LaTeX article using the standard article class.
- references.bib: a BibTeX bibliography with at least 30 well-known, real references from the field (e.g., Wei et al. 2022 chain-of-thought, Kojima et al. 2022 zero-shot CoT, Wang et al. 2022 self-consistency, Yao et al. 2023 ReAct, Yao et al. 2023 Tree of Thoughts, Besta et al. 2024 Graph of Thoughts, Li et al. 2023 structured CoT, Suzgun et al. 2022 BIG-Bench Hard, Fu et al. 2022 complexity-based prompting, Zhou et al. 2022 least-to-most prompting, Press et al. 2022 self-ask, Shinn et al. 2023 Reflexion, Madaan et al. 2023 self-refine, Lightman et al. 2023 process reward models, Uesato et al. 2022 chain-of-thought reasoning and intermediate rewards, Zelikman et al. 2022 STaR, Huang et al. 2022 large language models can self-improve, etc.).

Requirements for main.tex:
1. Use \documentclass[11pt,a4paper]{article}, with packages amsmath, amssymb, amsthm, graphicx, xcolor, hyperref, natbib or biblatex, booktabs, algorithm, algpseudocode, and any others you need.
2. Include title, author placeholder, abstract of ~250 words, and a table of contents is optional.
3. Structure: Introduction, Preliminaries, Chain-of-Thought Prompting (manual, zero-shot, automatic), Advanced Reasoning Structures (self-consistency, tree/graph/search-of-thought, tool-augmented reasoning), Training-Based Methods (supervised fine-tuning, RLHF, process rewards, STaR, self-improvement), Theoretical Perspectives (why CoT works, emergent abilities, scaling, coverage/bias), Applications (math, code, science, planning, agents), Challenges and Future Directions, Conclusion.
4. Include at least 3 high-level diagrams described in TikZ or simple LaTeX figures (you may use ASCII art inside a verbatim or tikzpicture). Include at least 2 algorithm pseudocode blocks.
5. Every substantive claim must be backed by a citation using \cite{} keys that exist in references.bib. Do not invent citations.
6. The document should be approximately 8,000–12,000 words (not counting references), organized into sections and subsections with clear narrative flow.
7. Use LaTeX math notation where appropriate.
8. Conclude with \bibliography{references} or \printbibliography.

Requirements for references.bib:
- At least 30 entries.
- Each entry must have author, title, year, and venue/booktitle/journal fields.
- Use standard BibTeX types: @article, @inproceedings, @book, @misc.
- Keys should be mnemonic (e.g., wei2022chain, kojima2022large).

Since no web search tools are available, rely on your training knowledge. Be accurate, but if you are uncertain about exact venue names or years, use your best recollection and keep entries internally consistent with the main.tex citations. Prefer real, well-known papers in the area.

After writing both files, read them back to verify they compile logically (no missing \cite keys, no syntax errors) and report the final file paths and a brief summary. Do not stop until both files are written and verified.
"""


def load_config() -> dict:
    """Read the local config.toml for LLM credentials."""
    import tomllib

    cfg_path = Path(__file__).resolve().parent.parent / "config.toml"
    if not cfg_path.exists():
        print(f"ERROR: {cfg_path} not found")
        sys.exit(1)
    with cfg_path.open("rb") as f:
        return tomllib.load(f)


def create_session(cfg: dict) -> str:
    large = cfg.get("models", {}).get("large", {})
    payload = {
        "base_url": large.get("base_url"),
        "api_key": large.get("api_key"),
        "model": large.get("model"),
        "context_window": large.get("context_window"),
        "workspace": str(WORKSPACE),
        "plan_mode": False,
        "max_iterations": 60,
        "mcp_servers": [],
        "api_keys": cfg.get("api_keys", {}),
    }
    r = httpx.post(f"{BASE}/api/sessions", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    print("created session:", data)
    return data["id"]


def stream_run(sid: str) -> None:
    url = f"{BASE}/api/sessions/{sid}/stream-sse"
    print(f"starting SSE run for session {sid} ...")
    start = time.time()
    with httpx.Client(timeout=None) as client:
        with client.stream("POST", url, json={"prompt": PROMPT}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    print("raw:", payload)
                    continue
                t = event.get("type")
                if t == "assistant_text":
                    print(event.get("text", ""), end="", flush=True)
                elif t == "tool_calls":
                    for tc in event.get("tool_uses", []):
                        print(f"\n[tool] {tc.get('name')}({tc.get('input', {})})")
                elif t == "tool_results":
                    for tr in event.get("results", []):
                        content = tr.get("content", "")
                        preview = content[:200] if isinstance(content, str) else str(content)[:200]
                        print(f"[result] {preview}...")
                elif t == "iteration":
                    print(f"\n--- iteration {event.get('n')} ---")
                elif t == "complete":
                    print("\n--- complete ---")
                    print(json.dumps(event, ensure_ascii=False, indent=2))
                    break
                elif t == "error":
                    print("\n--- error ---")
                    print(json.dumps(event, ensure_ascii=False, indent=2))
                    break
                else:
                    print(f"[{t}] {event}")
    elapsed = time.time() - start
    print(f"\nelapsed: {elapsed:.1f}s")


def copy_outputs() -> None:
    src_tex = WORKSPACE / "main.tex"
    src_bib = WORKSPACE / "references.bib"
    if not src_tex.exists():
        print("ERROR: main.tex not found in workspace")
        sys.exit(1)
    if not src_bib.exists():
        print("ERROR: references.bib not found in workspace")
        sys.exit(1)
    dest_dir = Path("/Volumes/ssd/main_link/work/Axiom/samples/survey")
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_tex, dest_dir / "main.tex")
    shutil.copy2(src_bib, dest_dir / "references.bib")
    print(f"copied survey to {dest_dir}")


if __name__ == "__main__":
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    sid = create_session(cfg)
    stream_run(sid)
    copy_outputs()
