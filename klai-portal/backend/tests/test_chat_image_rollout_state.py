"""Staged chat image rollout: the state file the rollout writes on the host.

The repository pins ONE chat image (portal-api's LIBRECHAT_IMAGE, the target).
deploy/scripts/chat-image-rollout.sh moves the fleet there in steps and records
each step in /opt/klai/librechat/image-rollout.json, which portal-api reads per
container create through its /librechat mount. While that file exists it, and
not the env default, decides which image a provisioning-managed tenant gets, so
an unproven target cannot reach a tenant through provisioning or an MCP-config
recreate while the canary is still being judged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.librechat_images import (
    ImageOverrideError,
    load_image_rollout_state,
)
from app.services.provisioning import infrastructure

STABLE = "ghcr.io/getklai/librechat@sha256:" + "1" * 64
CANDIDATE = "ghcr.io/getklai/librechat@sha256:" + "2" * 64
OTHER = "ghcr.io/getklai/librechat@sha256:" + "3" * 64


def _write(tmp_path: Path, **state: object) -> Path:
    path = tmp_path / "image-rollout.json"
    path.write_text(json.dumps(state))
    return path


class TestStateResolution:
    def test_canary_phase_moves_only_the_canary_tenant(self, tmp_path):
        state = load_image_rollout_state(
            _write(tmp_path, phase="canary", stable=STABLE, candidate=CANDIDATE, canary="e2e")
        )
        assert state is not None
        assert state.image_for("e2e") == CANDIDATE
        assert state.image_for("acme") == STABLE

    def test_fleet_phase_moves_every_tenant(self, tmp_path):
        state = load_image_rollout_state(
            _write(tmp_path, phase="fleet", stable=STABLE, candidate=CANDIDATE, canary="e2e")
        )
        assert state is not None
        assert state.image_for("acme") == CANDIDATE

    def test_rejected_candidate_reaches_no_tenant(self, tmp_path):
        state = load_image_rollout_state(
            _write(tmp_path, phase="rejected", stable=STABLE, candidate=CANDIDATE, canary="e2e")
        )
        assert state is not None
        assert state.image_for("e2e") == STABLE

    def test_missing_file_means_no_rollout_state(self, tmp_path):
        assert load_image_rollout_state(tmp_path / "image-rollout.json") is None

    def test_tag_reference_fails_loudly(self, tmp_path):
        path = _write(
            tmp_path, phase="canary", stable=STABLE, candidate="ghcr.io/getklai/librechat:v0.8.9", canary="e2e"
        )
        with pytest.raises(ImageOverrideError, match="digest"):
            load_image_rollout_state(path)

    def test_unknown_phase_fails_loudly(self, tmp_path):
        with pytest.raises(ImageOverrideError, match="phase"):
            load_image_rollout_state(_write(tmp_path, phase="halfway", stable=STABLE))


class TestTenantImage:
    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infrastructure.settings, "librechat_container_data_path", str(tmp_path))
        monkeypatch.setattr(infrastructure.settings, "librechat_image", CANDIDATE)
        monkeypatch.setattr(infrastructure.settings, "librechat_image_overrides", "")
        return tmp_path

    def test_state_file_beats_the_env_target(self, data_dir):
        _write(data_dir, phase="canary", stable=STABLE, candidate=CANDIDATE, canary="e2e")
        assert infrastructure._tenant_librechat_image("acme") == STABLE

    def test_without_state_file_the_env_default_applies(self, data_dir):
        assert infrastructure._tenant_librechat_image("acme") == CANDIDATE

    def test_explicit_env_override_beats_the_state_file(self, data_dir, monkeypatch):
        _write(data_dir, phase="stable", stable=STABLE)
        monkeypatch.setattr(infrastructure.settings, "librechat_image_overrides", f"acme={OTHER}")
        assert infrastructure._tenant_librechat_image("acme") == OTHER
