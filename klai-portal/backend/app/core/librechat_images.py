"""Per-tenant LibreChat image selection.

SPEC-LIBRECHAT-PATCH-MODEL-001. Provisioning-managed tenants all took
``settings.librechat_image``, so the only way to run one tenant on a new image
was to move all 42. That made "canary" a synonym for "declared in
docker-compose.yml", which fits exactly one tenant (getklai) -- and getklai
serves almost no traffic, so it could not produce the evidence a canary exists
to produce.

An override maps ``slug -> image``. Digest-only by design: on 2026-08-14 the
tag ``v0.8.7-klai.1`` was pushed twice with different content, so a
tag-referenced canary cannot be rolled back to what it was actually running.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "ImageOverrideError",
    "parse_librechat_image_overrides",
    "resolve_librechat_image",
]

_DIGEST_REF: Final = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class ImageOverrideError(ValueError):
    """Raised when the override string is malformed or not digest-pinned."""


def parse_librechat_image_overrides(raw: str) -> dict[str, str]:
    """Parse ``slug=image[,slug=image]`` into a mapping.

    Fails loud on anything ambiguous rather than dropping entries: a canary
    that silently did not take is worse than a deploy that refuses to start,
    because it looks like evidence while producing none.
    """
    overrides: dict[str, str] = {}
    if not raw or not raw.strip():
        return overrides

    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ImageOverrideError(f"malformed LibreChat image override {entry!r}: expected slug=image")
        slug, _, image = entry.partition("=")
        slug, image = slug.strip(), image.strip()
        if not slug:
            raise ImageOverrideError(f"empty slug in LibreChat image override {entry!r}")
        if not image:
            raise ImageOverrideError(f"empty image in LibreChat image override {entry!r}")
        if slug in overrides:
            raise ImageOverrideError(
                f"duplicate LibreChat image override for slug {slug!r}; "
                "two entries for one tenant means one of them is not what you think"
            )
        if not _DIGEST_REF.match(image):
            raise ImageOverrideError(
                f"LibreChat image override for {slug!r} must be pinned by digest "
                f"(name@sha256:<64 hex>), got {image!r}. A tag can be moved -- "
                "v0.8.7-klai.1 was overwritten in place on 2026-08-14 -- so a "
                "tag-pinned canary cannot be rolled back to what it ran."
            )
        overrides[slug] = image

    return overrides


def resolve_librechat_image(slug: str, default_image: str, overrides: dict[str, str]) -> str:
    """The image this tenant should run: its override, else the fleet default."""
    return overrides.get(slug, default_image)
