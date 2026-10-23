"""Settings read from the environment."""

import pytest

from retrieval_api.config import Settings


def test_source_preference_boost_env_override(monkeypatch):
    monkeypatch.setenv("SOURCE_PREFERENCE_BOOST", "0.08")

    assert Settings().source_preference_boost == pytest.approx(0.08)
