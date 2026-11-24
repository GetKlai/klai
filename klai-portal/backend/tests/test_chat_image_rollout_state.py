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


class _Container:
    def __init__(self, image: str) -> None:
        self.attrs = {"Image": image}


class _Client:
    """Answers the one list call the fleet fallback makes."""

    def __init__(self, *images: str) -> None:
        self.images = images
        self.filters: dict | None = None

    @property
    def containers(self):
        return self

    def list(self, *, all: bool, sparse: bool, filters: dict):
        self.filters = filters
        return [_Container(image) for image in self.images]


class TestTenantImage:
    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infrastructure.settings, "librechat_container_data_path", str(tmp_path))
        monkeypatch.setattr(infrastructure.settings, "librechat_image", CANDIDATE)
        monkeypatch.setattr(infrastructure.settings, "librechat_image_overrides", "")
        return tmp_path

    def test_state_file_beats_the_env_target(self, data_dir):
        _write(data_dir, phase="canary", stable=STABLE, candidate=CANDIDATE, canary="e2e")
        assert infrastructure._tenant_librechat_image("acme", _Client(STABLE)) == STABLE

    def test_reverting_hands_out_stable_while_the_put_back_runs(self, data_dir):
        _write(data_dir, phase="reverting", stable=STABLE, candidate=CANDIDATE, canary="e2e", next="rejected")
        assert infrastructure._tenant_librechat_image("e2e", _Client(STABLE)) == STABLE

    def test_without_state_file_the_fleet_image_not_the_newer_pin(self, data_dir):
        # The compose deploy has already put the new pin in the env; nothing
        # has proven it, so a create must keep getting what the fleet runs.
        client = _Client(STABLE, STABLE)
        assert infrastructure._tenant_librechat_image("acme", client) == STABLE
        assert client.filters == {"label": ["klai.managed_by=portal-api-provisioning", "klai.kind=librechat"]}

    def test_without_state_file_a_mixed_fleet_fails_closed(self, data_dir):
        with pytest.raises(RuntimeError, match="more than one image"):
            infrastructure._tenant_librechat_image("acme", _Client(STABLE, OTHER))

    def test_without_state_file_or_fleet_the_env_default_applies(self, data_dir):
        assert infrastructure._tenant_librechat_image("acme", _Client()) == CANDIDATE

    def test_explicit_env_override_beats_the_state_file(self, data_dir, monkeypatch):
        _write(data_dir, phase="stable", stable=STABLE)
        monkeypatch.setattr(infrastructure.settings, "librechat_image_overrides", f"acme={OTHER}")
        assert infrastructure._tenant_librechat_image("acme", _Client(STABLE)) == OTHER


class TestImageResolvedBeforeRemoval:
    def test_a_corrupt_state_file_leaves_the_running_container_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infrastructure.settings, "librechat_container_data_path", str(tmp_path))
        monkeypatch.setattr(infrastructure.settings, "librechat_image_overrides", "")
        (tmp_path / "image-rollout.json").write_text("{not json")

        class Old:
            removed = False

            def __init__(self) -> None:
                self.attrs = {"Config": {"Image": STABLE}}

            def remove(self, force):
                Old.removed = True

        class Client(_Client):
            def get(self, name):
                return Old()

        monkeypatch.setattr(infrastructure.docker, "from_env", lambda: Client(STABLE))
        with pytest.raises(ImageOverrideError):
            infrastructure._start_librechat_container("acme", "/opt/klai/librechat/acme/.env", rollback_on_failure=True)
        assert Old.removed is False
