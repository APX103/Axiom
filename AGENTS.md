# Axiom Agent Instructions

## Git Workflow

- **Never commit directly to `main`.** All changes must go through `dev` first.
- Commit features/fixes to `dev` with descriptive messages.
- Releases are done by squash-rebasing `dev` onto `main` and tagging (e.g., `v0.0.9`).
- Do not fast-forward `main` with individual dev commits unless explicitly asked to prepare a release.
- **Force-push to `main` / `dev` is prohibited unless explicitly authorized.** If history must be rewritten (e.g., a botched release commit), get explicit approval first and do it only as a last resort.

## Versioning

- **Default: bump the patch version for every release** (`0.0.11` → `0.0.12`).
- **Do not bump minor or major versions unless explicitly instructed.** Wait for user confirmation before changing `0.x.0` or `x.0.0`.

## Release Commits

- A release must result in **exactly one clean release commit** on `main`.
- The release commit message should follow the format:
  - Title: `release: v0.0.X — concise summary of key changes`
  - Body: detailed bullet points covering bug fixes, UI/UX changes, backend changes, and release housekeeping.
- Avoid multiple `release: v0.0.X` commits in a row. If a release is botched, fix it cleanly (rewrite history if authorized) rather than piling on fixup release commits.

## Build & Release

- macOS app: run `bash scripts/build_mac_app.sh`.
- Frontend UI variants: `classic` (default) and `v2` (modern aurora-glass theme).
  - Dev: `cd frontend && bun run dev` / `bun run dev:v2`
  - Build: `cd frontend && bun run build` / `bun run build:v2`
  - macOS app: `UI_VARIANT=v2 bash scripts/build_mac_app.sh`
  - Implementation: build-time flag `VITE_UI_VARIANT`; `main.tsx` adds `ui-v2` class to `<html>`, all v2 styles live scoped in `frontend/src/v2/theme-v2.css`.
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
