"""Per-tenant LibreChat image overrides (canary rollout).

Every provisioning-managed tenant used to get `settings.librechat_image`, so
there was no way to run one tenant on a new image without moving all 42. That
made "canary" mean "a container declared in compose", which only worked for
getklai -- and getklai serves no traffic, so it proved nothing over time.

An override maps slug -> image. It is deliberately digest-only: a canary you
cannot roll back to is not a canary, and 2026-08-14 showed a tag being
overwritten in place.
"""

from __future__ import annotations

import pytest

from app.core.librechat_images import (
    ImageOverrideError,
    parse_librechat_image_overrides,
    resolve_librechat_image,
)

DIGEST = "sha256:" + "a" * 64
KLAI_IMAGE = f"ghcr.io/getklai/librechat@{DIGEST}"
DEFAULT = "ghcr.io/danny-avila/librechat:v0.8.7"


class TestParsing:
    def test_empty_means_no_overrides(self):
        assert parse_librechat_image_overrides("") == {}
        assert parse_librechat_image_overrides("   ") == {}

    def test_single_and_multiple_entries(self):
        assert parse_librechat_image_overrides(f"voys={KLAI_IMAGE}") == {"voys": KLAI_IMAGE}
        parsed = parse_librechat_image_overrides(f"voys={KLAI_IMAGE}, getklai={KLAI_IMAGE}")
        assert parsed == {"voys": KLAI_IMAGE, "getklai": KLAI_IMAGE}

    def test_rejects_a_tag_reference(self):
        # The whole point: an override you cannot roll back to is worthless.
        with pytest.raises(ImageOverrideError, match="digest"):
            parse_librechat_image_overrides("voys=ghcr.io/getklai/librechat:v0.8.7-klai.1")

    def test_rejects_a_truncated_digest(self):
        with pytest.raises(ImageOverrideError, match="digest"):
            parse_librechat_image_overrides("voys=ghcr.io/getklai/librechat@sha256:abc123")

    def test_rejects_malformed_entries(self):
        with pytest.raises(ImageOverrideError, match="slug=image"):
            parse_librechat_image_overrides("voys")

    def test_rejects_an_empty_slug(self):
        with pytest.raises(ImageOverrideError, match="slug"):
            parse_librechat_image_overrides(f"={KLAI_IMAGE}")

    def test_rejects_a_duplicate_slug(self):
        # Two entries for one tenant means somebody edited without looking;
        # silently taking the last one is how a canary lands on the wrong image.
        with pytest.raises(ImageOverrideError, match="duplicate"):
            parse_librechat_image_overrides(f"voys={KLAI_IMAGE},voys={KLAI_IMAGE}")


class TestResolution:
    def test_tenant_without_override_gets_the_fleet_default(self):
        assert resolve_librechat_image("acme", DEFAULT, {"voys": KLAI_IMAGE}) == DEFAULT

    def test_tenant_with_override_gets_it(self):
        assert resolve_librechat_image("voys", DEFAULT, {"voys": KLAI_IMAGE}) == KLAI_IMAGE

    def test_no_overrides_at_all(self):
        assert resolve_librechat_image("voys", DEFAULT, {}) == DEFAULT
