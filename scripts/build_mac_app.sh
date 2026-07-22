#!/usr/bin/env bash
# 构建 Axiom macOS 桌面应用。
# 输出: src-tauri/target/release/bundle/macos/Axiom.app
#       src-tauri/target/release/bundle/dmg/Axiom_*.dmg

set -euo pipefail

# 确保 cargo 在 PATH 中 (部分环境 rustup 未默认加 PATH)
if [ -d "$HOME/.rustup/toolchains" ]; then
  CARGO_BIN=$(find "$HOME/.rustup/toolchains" -name cargo -type f 2>/dev/null | head -1)
  if [ -n "$CARGO_BIN" ]; then
    export PATH="$(dirname "$CARGO_BIN"):$PATH"
  fi
fi

cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "[build] cleaning old artifacts..."
rm -rf "${ROOT}/dist/operon-backend"
rm -rf "${ROOT}/build/operon-backend"
rm -rf "${ROOT}/frontend/dist"

echo "[build] syncing dependencies..."
uv sync

echo "[build] building Python backend with PyInstaller..."
uv run pyinstaller operon-backend.spec --clean --noconfirm

echo "[build] building frontend..."
cd "${ROOT}/frontend"
bun install --frozen-lockfile 2>/dev/null || bun install
bun run build

echo "[build] building Tauri bundle..."
cd "${ROOT}/src-tauri"
bun x @tauri-apps/cli build

echo "[build] signing with entitlements..."
# 给 operon-backend 和整个 APP 用 adhoc 签名 + entitlements
# 未签名 APP 通过 open 启动时, macOS 会限制子进程网络绑定
codesign --force --sign - --entitlements "${ROOT}/src-tauri/entitlements.plist" \
  "${ROOT}/src-tauri/target/release/bundle/macos/Axiom.app/Contents/Resources/operon-backend" 2>&1
codesign --force --deep --sign - --entitlements "${ROOT}/src-tauri/entitlements.plist" \
  "${ROOT}/src-tauri/target/release/bundle/macos/Axiom.app" 2>&1

echo "[build] done."
VERSION=$(grep '^version' "${ROOT}/src-tauri/Cargo.toml" | head -1 | sed 's/.*= *"\(.*\)".*/\1/')
echo "  App:  ${ROOT}/src-tauri/target/release/bundle/macos/Axiom.app"
echo "  DMG:  ${ROOT}/src-tauri/target/release/bundle/dmg/Axiom_${VERSION}_aarch64.dmg"
