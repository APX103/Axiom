"""Provider 热切换的回归测试。

之前: 设置里切换启用的 provider 后, 已驻内存的会话仍用建会话时的旧 client,
必须重启 (走 restore 重建) 才生效。现在 SessionManager.run 每次执行前调
_refresh_provider_if_changed: client 未被外部替换 且 settings 变了 → 重建 client。
"""

from __future__ import annotations

from types import SimpleNamespace

import axiom_core.config
from axiom_core.api.sessions import SessionManager


class _Tier:
    def __init__(self, model: str, base_url: str, api_key: str, context_window: int = 128000):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.context_window = context_window


class _Models:
    def __init__(self, tier: _Tier):
        self._tier = tier

    def tier(self, _name: str) -> _Tier:
        return self._tier


class _Settings:
    def __init__(self, tier: _Tier):
        self.models = _Models(tier)
        self.default_model_tier = "large"


def _fake_active(model: str, base_url: str, api_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="s1",
        session=SimpleNamespace(
            llm=SimpleNamespace(base_url=base_url, api_key=api_key),
            config=SimpleNamespace(model=model, context_window=256000),
        ),
        # 建会话时的 provider 快照 (与当前 client 一致 = client 没被外部动过)
        provider_snapshot=(model, base_url, api_key),
    )


def _patch_settings(monkeypatch, tier: _Tier):
    monkeypatch.setattr(axiom_core.config, "load_settings", lambda *a, **k: _Settings(tier))


def test_hot_swap_on_provider_change(monkeypatch):
    _patch_settings(monkeypatch, _Tier("model-B", "https://b.example/v1", "real-B"))
    active = _fake_active("model-A", "https://a.example/v1", "real-A")

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is True

    assert active.session.config.model == "model-B"
    assert active.session.config.context_window == 128000
    assert active.session.llm.base_url == "https://b.example/v1"
    assert active.session.llm.api_key == "real-B"
    assert active.provider_snapshot == ("model-B", "https://b.example/v1", "real-B")

    # 已一致 → 不再切换
    assert mgr._refresh_provider_if_changed(active) is False


def test_hot_swap_on_key_rotation_same_model(monkeypatch):
    """同 model 同 base_url 但 key 换了 → 也要重建 client。"""
    _patch_settings(monkeypatch, _Tier("model-A", "https://a.example/v1", "real-A-NEW"))
    active = _fake_active("model-A", "https://a.example/v1", "real-A-OLD")

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is True
    assert active.session.llm.api_key == "real-A-NEW"


def test_no_swap_when_unchanged(monkeypatch):
    _patch_settings(monkeypatch, _Tier("model-A", "https://a.example/v1", "real-A"))
    active = _fake_active("model-A", "https://a.example/v1", "real-A")

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is False


def test_no_swap_when_client_replaced_externally(monkeypatch):
    """client 被外部替换过 (快照不匹配, 如测试注入 FakeLLM) → 不动。"""
    _patch_settings(monkeypatch, _Tier("model-B", "https://b.example/v1", "real-B"))
    active = _fake_active("model-A", "https://a.example/v1", "real-A")
    # 模拟外部把 client 换成了别的对象 (无 base_url/api_key 属性)
    active.session.llm = SimpleNamespace()

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is False


def test_no_crash_without_models(monkeypatch):
    """settings 无 models 配置时静默跳过, 不影响 run。"""
    monkeypatch.setattr(
        axiom_core.config,
        "load_settings",
        lambda *a, **k: SimpleNamespace(models=None, default_model_tier="large"),
    )
    active = _fake_active("model-A", "https://a.example/v1", "real-A")

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is False
    assert active.session.config.model == "model-A"


def test_no_crash_on_settings_error(monkeypatch):
    """load_settings 抛异常也不阻断 run (保留旧 client)。"""
    def _boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(axiom_core.config, "load_settings", _boom)
    active = _fake_active("model-A", "https://a.example/v1", "real-A")

    mgr = SessionManager()
    assert mgr._refresh_provider_if_changed(active) is False
    assert active.session.config.model == "model-A"
