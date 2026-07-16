# Axiom Agent Instructions

## Git Workflow

- **Never commit directly to `main`.** All changes must go through `dev` first.
- Commit features/fixes to `dev` with descriptive messages.
- Releases are done by squash-rebasing `dev` onto `main` and tagging (e.g., `v0.0.9`).
- Do not fast-forward `main` with individual dev commits unless explicitly asked to prepare a release.

## Build & Release

- macOS app: run `bash scripts/build_mac_app.sh`.
- Bump version in all relevant files before a release:
  - `pyproject.toml`
  - `frontend/package.json`
  - `src-tauri/Cargo.toml`
  - `src-tauri/tauri.conf.json`
  - sync `uv.lock` with `uv sync`
  - sync `src-tauri/Cargo.lock` with `cargo update -p axiom`
- Tag format: `v0.0.X` with an annotated tag.

## Code Quality

- Run `uv run pytest -q` for Python changes.
- Run `uv run ruff check ...` for linting.
- Run `cd frontend && bun run build` for frontend changes.
