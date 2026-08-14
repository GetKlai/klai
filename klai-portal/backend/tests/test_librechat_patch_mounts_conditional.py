"""Patch bind-mounts must not be layered on top of an image that bakes them in.

SPEC-LIBRECHAT-PATCH-MODEL-001 Phase 5. A bind-mount wins over the file in the
image, so a tenant recreated on the Klai-owned image WITH the four patch mounts
still attached would serve the old hand-edited bundles -- the migration would
apply to nothing while looking complete. That is the same failure shape as the
createStreamServices mount that sat inert for months.

Mounting stays conditional rather than deleted: rolling the fleet back to the
upstream image must restore a patched LibreChat, and upstream only has the
patches via those mounts.
"""

from __future__ import annotations

from app.services.provisioning.infrastructure import image_bakes_in_patches

UPSTREAM = "ghcr.io/danny-avila/librechat:v0.8.7"
KLAI = "ghcr.io/getklai/librechat@sha256:" + "b" * 64


def test_upstream_image_still_needs_the_mounts():
    assert image_bakes_in_patches(UPSTREAM) is False


def test_klai_image_carries_them_already():
    assert image_bakes_in_patches(KLAI) is True


def test_klai_image_by_tag_also_counts():
    # Tags are rejected elsewhere (check-klai-librechat-digest.sh); this
    # function answers "does this image carry the patches", not "is this
    # reference acceptable" -- conflating the two would mount patches on top of
    # a Klai image that someone pinned badly.
    assert image_bakes_in_patches("ghcr.io/getklai/librechat:v0.8.7-klai.1") is True


def test_unrelated_registry_is_treated_as_needing_mounts():
    # Fail safe: an image we do not recognise gets the mounts, because serving
    # an unpatched LibreChat is worse than a redundant mount.
    assert image_bakes_in_patches("registry.example.com/someone/librechat:1") is False
