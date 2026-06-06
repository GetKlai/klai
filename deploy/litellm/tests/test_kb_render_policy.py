from __future__ import annotations

import pytest

import klai_kb_render_policy as policy


def test_resolve_kb_render_mode_accepts_only_deterministic_non_streaming():
    assert policy.resolve_kb_render_mode(None) == policy.KB_RENDER_MODE_STREAMING_GUARD
    assert policy.resolve_kb_render_mode("") == policy.KB_RENDER_MODE_STREAMING_GUARD
    assert (
        policy.resolve_kb_render_mode(policy.KB_RENDER_MODE_LEGACY_STREAMING_GUARD)
        == policy.KB_RENDER_MODE_STREAMING_GUARD
    )
    assert policy.resolve_kb_render_mode("garbage") == policy.KB_RENDER_MODE_STREAMING_GUARD
    assert (
        policy.resolve_kb_render_mode(policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING)
        == policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING
    )


def test_is_streaming_kb_render_mode_includes_legacy_alias():
    assert policy.is_streaming_kb_render_mode(policy.KB_RENDER_MODE_STREAMING_GUARD) is True
    assert policy.is_streaming_kb_render_mode(policy.KB_RENDER_MODE_LEGACY_STREAMING_GUARD) is True
    assert (
        policy.is_streaming_kb_render_mode(policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING)
        is False
    )


def test_select_kb_render_strategy_preserves_streaming_requests():
    strategy = policy.select_kb_render_strategy(
        True,
        configured_mode=policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
    )

    assert strategy.mode == policy.KB_RENDER_MODE_STREAMING_GUARD
    assert strategy.force_non_streaming is False


def test_select_kb_render_strategy_forces_non_streaming_when_configured():
    strategy = policy.select_kb_render_strategy(
        False,
        configured_mode=policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING,
    )

    assert strategy.mode == policy.KB_RENDER_MODE_DETERMINISTIC_NON_STREAMING
    assert strategy.force_non_streaming is True


def test_select_kb_render_strategy_has_no_implicit_env_fallback():
    with pytest.raises(TypeError):
        policy.select_kb_render_strategy(False)
