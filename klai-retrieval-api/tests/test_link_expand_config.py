"""Tests for link expansion config settings (SPEC-CRAWLER-003, R19, R20)."""
import pytest


def test_link_expand_defaults():
    """Settings() with no env vars returns correct defaults (Scenario 8.1)."""
    from retrieval_api.config import Settings

    s = Settings()
    assert s.link_expand_enabled is True
    assert s.link_expand_seed_k == 10
    assert s.link_expand_max_urls == 30
    assert s.link_expand_candidates == 20
    assert s.link_authority_boost == 0.05


def test_link_expand_override(monkeypatch):
    """Settings overridable via environment variables (Scenario 8.2)."""
    monkeypatch.setenv("LINK_EXPAND_ENABLED", "false")
    monkeypatch.setenv("LINK_AUTHORITY_BOOST", "0.10")
    from importlib import reload

    import retrieval_api.config as cfg_mod

    reload(cfg_mod)
    s = cfg_mod.Settings()
    assert s.link_expand_enabled is False
    assert s.link_authority_boost == pytest.approx(0.10)
