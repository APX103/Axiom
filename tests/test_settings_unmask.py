"""脱敏 key 还原的回归测试。

前端从 GET /api/settings 只能拿到脱敏 key (如 "sk-****…ab"), 切换/保存后
可能把脱敏值原样送回后端。修复前 create_session 会把脱敏值当真实 key 用 → 401。
现在: unmask_key / unmask_from_candidates 负责还原; Bearer header 同样处理。
"""

from __future__ import annotations

from operon.settings import (
    mask_header_value,
    mask_key,
    unmask_from_candidates,
    unmask_key,
)

REAL = "sk-proj-abcdefghijklmnop1234567890"


def test_mask_key_roundtrip():
    masked = mask_key(REAL)
    assert masked == f"{REAL[:4]}****{REAL[-4:]}"
    assert unmask_key(masked, REAL) == REAL


def test_unmask_key_new_value_passthrough():
    """用户改了 key (不是 mask 形式) → 用新值。"""
    assert unmask_key("brand-new-key-0000", REAL) == "brand-new-key-0000"


def test_mask_key_short_and_empty():
    assert mask_key("") == ""
    assert mask_key(None) == ""
    short = "short-key"
    assert mask_key(short) == "*" * len(short)
    assert unmask_key(mask_key(short), short) == short


def test_mask_header_value_keeps_bearer_prefix():
    masked = mask_header_value(f"Bearer {REAL}")
    assert masked == f"Bearer {REAL[:4]}****{REAL[-4:]}"
    # 非 Bearer 的值走普通 mask
    assert mask_header_value(REAL) == mask_key(REAL)


def test_unmask_key_bearer_roundtrip():
    old = f"Bearer {REAL}"
    masked = mask_header_value(old)
    assert unmask_key(masked, old) == old


def test_unmask_from_candidates_picks_real():
    masked = mask_key(REAL)
    candidates = ["other-key-00000000", REAL, None]
    assert unmask_from_candidates(masked, candidates) == REAL


def test_unmask_from_candidates_bearer():
    old = f"Bearer {REAL}"
    masked = mask_header_value(old)
    assert unmask_from_candidates(masked, [old]) == old


def test_unmask_from_candidates_passthrough():
    """不在 candidates 里的 mask 形式不还原 (不是我们的 key)。"""
    assert unmask_from_candidates("zzzz****yyyy", [REAL]) == "zzzz****yyyy"
    assert unmask_from_candidates(None, [REAL]) is None
    assert unmask_from_candidates("", [REAL]) == ""
