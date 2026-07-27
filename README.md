# Axiom

> **Languages:** **English** · [简体中文](README.zh.md)

Axiom is a **local-first research AI workbench**. Built on the `axiom-core` kernel, it supports literature surveys, data analysis, mathematical modeling, and code experiments — with all execution and data staying on your machine. The autonomous long-horizon research loop borrows from [Deli Chen's auto-research protocol](TODO-add-link).

- **Local-first** — sessions, workspace files, and the SQLite database all live on your machine.
- **Cloud inference, local execution** — the LLM call goes to any OpenAI-compatible provider; tool execution, file I/O, and code runs happen locally.
- **Desktop client** — a Tauri v2 macOS app; launch it and you're in the workbench.
- **Academic workflow** — OpenAlex search, citation-graph expansion, DOI verification, and a full paper-writing chain (`paper-writing` orchestrates five sub-skills from graded bibliography to PDF typesetting).
- **LaTeX templates & PDF preview** — built-in IEEE / ACM / article templates with locked preambles (no more TikZ compile failures); TeX (KaTeX) and PDF (real Tectonic compile) dual-mode preview.
- **Long-horizon autonomous research** — the `deli-autoresearch` skill, adapted from Deli Chen's protocol, runs hours-long unattended research loops.

For the internal architecture, see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Quick start

1. Download the `.dmg` for your architecture from [GitHub Releases](https://github.com/APX103/Axiom/releases).
2. Open the `.dmg` and drag `Axiom.app` into **Applications**.
3. On first launch, right-click → **Open** while holding `Control` (the app is unsigned; this allows Gatekeeper).
4. Open **Settings**, fill in your LLM provider (Base URL + API Key + Model).
5. Close settings and start using it.

> For literature surveys, also fill in an **OpenAlex API Key** (free, https://openalex.org/settings/api).

---

## Configuration

### Required: LLM provider

In **Settings → Models**, add and enable a provider:

| Field | Example |
|-------|---------|
| Base URL | `https://api.stepfun.com/step_plan/v1` (any OpenAI-compatible endpoint) |
| API Key | your key |
| Model | `step-3.7-flash` / `deepseek-chat` / … |
| Context Window | the model's real context length, e.g. `256000` |
| Max Tokens | max output per turn, e.g. `8192` |

### Optional

- **MCP** — add MCP-compatible search services (e.g. Zhipu `web-search-prime`).
- **Academic** — OpenAlex API key (required for OpenAlex search; OpenAlex enforces keys from 2026-02).
- **Template** — pick a paper template for new sessions (article / IEEE / ACM).

Settings persist to `~/.axiom/settings.json`. Developers can also edit a root `config.toml` (gitignored). Priority: `env (AXIOM_*) > settings.json > config.toml > defaults`.

---

## Usage

### Chat & tool calls

Type a question; Axiom calls the right tools — file ops, Python/Bash execution, web/paper search, MCP tools — and streams the process live.

### Plan mode

For complex tasks, enable **Plan Mode** (Settings → General, or prefix a message with `/plan `). Axiom drafts a multi-step plan and waits for your approval before executing.

### Literature surveys & paper writing

A prompt like `帮我写一份关于 "chain-of-thought reasoning in LLMs" 的综述` triggers the `paper-writing` orchestrator: graded literature recall → citation expansion → DOI verification → structured writing, compiled to a real PDF.

### LaTeX preview

In a workspace with a `.tex` file, click **查看论文** to open PaperView:

- **TeX mode** — structure + KaTeX math, instant.
- **PDF mode** — compiles via [Tectonic](https://tectonic-typesetting.github.io/) and renders the real PDF with PDF.js (exact typesetting). Install Tectonic: `brew install tectonic`.

Compile failures show the specific error line parsed from the `.log`.

---

## Build & develop

Requirements: macOS 10.13+, Python 3.11+, [uv](https://docs.astral.sh/uv/), [bun](https://bun.sh/), [Rust](https://rustup.rs/).

```bash
uv sync                              # Python deps
cd frontend && bun install           # Frontend deps
uv run axiom serve                  # Backend (dev)
cd frontend && bun run dev           # Frontend (dev)
uv run pytest                        # Tests
./scripts/build_mac_app.sh           # Build the macOS app
```

---

## Release flow

> Full rules in [AGENTS.md](AGENTS.md).

Versioning is single-source (`package.json` / `pyproject.toml` / `Cargo.toml` / `tauri.conf.json` must agree). On release, dev commits are squash-merged into `main` as one clean release commit, an annotated `v0.0.x` tag is pushed, and [GitHub Actions](.github/workflows/release.yml) builds + publishes a proper Release (not a draft) with `latest.json` for update checks. A CI version-consistency check fails the build if any source disagrees with the tag.

---

## Built-in skills

Axiom ships a research-writing skill chain orchestrated by `paper-writing`: `lit-survey` (graded bibliography), `paper-structure` (skeleton & logic), `academic-figures` (tables/plots), `experiment-design` (original analysis), `peer-review` (scoring loop). Plus `deli-autoresearch` for long-horizon loops, `pdf-explore`, `literature-review`, and more. See [ARCHITECTURE.md](docs/ARCHITECTURE.md#内置-skills) for the full list and division of labor.

---

## Troubleshooting

- **"服务离线"** — open Settings, confirm a valid LLM provider is saved; wait 2–3s for reconnect, or launch from terminal to see backend logs: `/Applications/Axiom.app/Contents/MacOS/Axiom`.
- **App won't open on another Mac** — check architecture match; use `Control + right-click → Open`; if a stale unsigned build lingers, `xattr -cr /Applications/Axiom.app`.
- **OpenAlex search fails** — add the OpenAlex API key in Settings → Academic, or set `OPENALEX_API_KEY`.
- **DMG packaging fails locally** — detach a leftover mount: `hdiutil detach "/Volumes/dmg.xxx" -force`.

---

## License

MIT
