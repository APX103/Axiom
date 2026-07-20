#!/usr/bin/env python3
"""版本一致性检查。

检查 4 个文件的版本号是否一致:
- pyproject.toml
- frontend/package.json
- src-tauri/Cargo.toml
- src-tauri/tauri.conf.json

用法:
    # 检查 4 个文件之间是否一致 (CI PR check 用)
    python scripts/check_version.py

    # 检查 4 个文件是否都等于某个期望值 (release tag 用)
    EXPECTED=0.0.14 python scripts/check_version.py --expected $EXPECTED
    # 或
    python scripts/check_version.py --expected 0.0.14

退出码:
    0 = 全部一致
    1 = 有不一致

从 .github/workflows/release.yml 抽出来, CI 和 release 都能复用。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 文件 → 提取版本的正则 + 描述
# 用 head-1 兜底依赖列表里可能出现的同名 key
SOURCES: list[tuple[str, Path, str, str]] = [
    (
        "pyproject.toml",
        REPO_ROOT / "pyproject.toml",
        r'^version\s*=\s*"([^"]+)"',
        "version = \"X.Y.Z\" (顶层 [project] 段)",
    ),
    (
        "frontend/package.json",
        REPO_ROOT / "frontend" / "package.json",
        r'"version"\s*:\s*"([^"]+)"',
        '"version": "X.Y.Z"',
    ),
    (
        "src-tauri/Cargo.toml",
        REPO_ROOT / "src-tauri" / "Cargo.toml",
        r'^version\s*=\s*"([^"]+)"',
        "version = \"X.Y.Z\" ([package] 段)",
    ),
    (
        "src-tauri/tauri.conf.json",
        REPO_ROOT / "src-tauri" / "tauri.conf.json",
        r'"version"\s*:\s*"([^"]+)"',
        '"version": "X.Y.Z"',
    ),
]


def extract_version(path: Path, pattern: str) -> str | None:
    """从文件中用正则提取第一个匹配的版本号。"""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 4 个版本文件是否一致。")
    parser.add_argument(
        "--expected",
        default=os.environ.get("EXPECTED"),
        help="期望的版本号 (默认从 EXPECTED 环境变量读; 不传则只检查 4 个文件之间一致)",
    )
    args = parser.parse_args()

    found: dict[str, str | None] = {}
    for label, path, pattern, _hint in SOURCES:
        found[label] = extract_version(path, pattern)

    print("版本号:")
    for label, version in found.items():
        print(f"  {label:30s} {version or '<未找到/文件缺失>'}")

    expected = args.expected
    if expected is not None:
        # release tag 模式: 全部应等于 expected
        failures = [
            (label, v) for label, v in found.items() if v != expected
        ]
        if failures:
            for label, v in failures:
                print(
                    f"::error::{label} 版本 '{v}' 不匹配期望 '{expected}'",
                    file=sys.stderr,
                )
            print(
                f"::error::把 package.json / pyproject.toml / Cargo.toml / "
                f"tauri.conf.json 全部改为 {expected} 再 tag。",
                file=sys.stderr,
            )
            return 1
        print(f"全部版本文件与 tag {expected} 一致。")
        return 0

    # CI 模式: 4 个文件之间应该一致 (取第一个作为基准)
    versions = [v for v in found.values() if v is not None]
    if not versions:
        print("::error::所有版本文件都缺失或解析失败", file=sys.stderr)
        return 1
    baseline = versions[0]
    failures = [(label, v) for label, v in found.items() if v != baseline]
    if failures:
        for label, v in failures:
            print(
                f"::error::{label} 版本 '{v}' 与基准 '{baseline}' 不一致",
                file=sys.stderr,
            )
        print(
            "::error::4 个版本文件之间不一致, 请统一 (参见 AGENTS.md 版本管理)。",
            file=sys.stderr,
        )
        return 1
    print(f"4 个版本文件一致: {baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
