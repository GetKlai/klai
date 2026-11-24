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

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "IMAGE_ROLLOUT_STATE_FILE",
    "ImageOverrideError",
    "ImageRolloutState",
    "load_image_rollout_state",
    "parse_librechat_image_overrides",
]

# Written only by deploy/scripts/chat-image-rollout-host.sh, in the shared
# LibreChat data directory that portal-api mounts at /librechat.
IMAGE_ROLLOUT_STATE_FILE: Final = "image-rollout.json"
_ROLLOUT_PHASES: Final = frozenset({"stable", "canary", "fleet", "reverting", "rejected"})

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


@dataclass(frozen=True)
class ImageRolloutState:
    """One step of a staged chat image rollout.

    ``stable`` is the image the fleet is proven on. ``canary`` moves only the
    canary tenant to ``candidate``; ``fleet`` moves everyone there while the
    second proof runs; ``reverting`` (a put-back in progress, not yet
    verified), ``rejected`` and ``stable`` keep everyone on ``stable``.
    """

    phase: str
    stable: str
    candidate: str
    canary: str

    def image_for(self, slug: str) -> str:
        if self.phase == "fleet" or (self.phase == "canary" and slug == self.canary):
            return self.candidate
        return self.stable


def load_image_rollout_state(path: Path) -> ImageRolloutState | None:
    """Read the rollout state, or None when no rollout has ever run.

    A file that exists but cannot be read as a valid state raises instead of
    returning None: falling back would hand every tenant the env target, which
    is exactly the unproven image the state file is holding back.
    """
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImageOverrideError(f"chat image rollout state {path} is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ImageOverrideError(f"chat image rollout state {path} is not a JSON object")

    phase = data.get("phase")
    if phase not in _ROLLOUT_PHASES:
        raise ImageOverrideError(f"chat image rollout state {path} has unknown phase {phase!r}")
    stable = str(data.get("stable") or "")
    candidate = str(data.get("candidate") or "")
    canary = str(data.get("canary") or "")
    required = {"stable": stable}
    if phase in ("canary", "fleet", "reverting"):
        required["candidate"] = candidate
    for field, image in required.items():
        if not _DIGEST_REF.match(image):
            raise ImageOverrideError(
                f"chat image rollout state {path}: {field} must be pinned by digest "
                f"(name@sha256:<64 hex>), got {image!r}"
            )
    if phase == "canary" and not canary:
        raise ImageOverrideError(f"chat image rollout state {path}: phase canary without a canary tenant")
    return ImageRolloutState(phase=phase, stable=stable, candidate=candidate, canary=canary)
