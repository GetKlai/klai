"""Single source of truth for KB-image URL + S3-key + FastAPI route shape.

SPEC-KB-IMAGES-V2-001 (replaces SPEC-TI-009).

Why this module exists
----------------------
Until 2026-05-12 three independent files each hardcoded part of the
kb-image URL shape:

- ``klai_image_storage.storage.PUBLIC_IMAGE_PATH_PREFIX`` + ``ImageStore.build_public_url``
- ``app/api/kb_images.py`` @router.get path-literal
- ``klai-portal/frontend/.../BlockPageEditor.tsx`` ``fetch(...)`` URL

Any drift between the three produced a *silent* 404 on browser fetches —
because the legacy SPEC-TI-009 route was never browser-fetched in
production, the drift went undetected for 3 weeks and required five
sequential bandaid PRs to remediate. This module collapses all three
hardcodings into a single value-class and a small set of constants that
are imported everywhere else.

How drift is prevented
----------------------
1. ``KbImage.ROUTE_TEMPLATE`` is the only string the FastAPI decorator
   references. A drift here is a Python-level error: pyright sees the
   constant rename, the route declaration breaks, the boot-time
   assertion in ``app.main`` (REQ-3) catches mismatch between the route
   declared and what ``KbImage(...).public_path`` produces.
2. ``rules/no-hardcoded-kb-image-path.yml`` (REQ-5) is a CI ast-grep
   guard that fails any PR adding a ``/kb-images/`` string-literal
   outside this module.
3. ``klai-portal/frontend/src/lib/kb-image-url.ts`` mirrors the same
   constants and has a vitest unit test that compares its outputs to
   fixture strings derived from this module — a Python<->TS drift is
   caught at unit-test time.
4. A Playwright E2E test in CI (REQ-6) drives the full Caddy -> portal-api
   -> Garage round-trip on every PR that touches this module or its
   callers.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import ClassVar

# Wire-level invariants — changing any of these invalidates every previously
# generated kb-image URL. SPEC-KB-IMAGE-002 + SPEC-KB-IMAGES-V2-001.
_MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_VALID_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_KB_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Zitadel org IDs are snowflake-style 18-digit numbers, but the wire-format
# accepts any 1..20 digit string so that older test fixtures (small ints)
# also parse.
_VALID_ZITADEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$|^[0-9]{1,20}$")
_VALID_EXT_RE = re.compile(r"^(jpg|png|gif|webp)$")


@dataclass(frozen=True, slots=True)
class KbImage:
    """A KB-image identified by its (org, kb, content-hash, extension) tuple.

    Construct via :py:meth:`from_bytes` (writes) or :py:meth:`from_path`
    (reads). Direct construction is allowed for tests but validates all
    fields against the regexes above.
    """

    zitadel_org_id: str
    kb_slug: str
    sha256: str
    ext: str

    # FastAPI route templates — the *only* strings the route decorators
    # may reference. Importers MUST use these constants verbatim:
    #
    #   @router.get(KbImage.ROUTE_TEMPLATE)
    #   @router.post(KbImage.UPLOAD_ROUTE_TEMPLATE)
    #
    # A drift here is caught by the boot-time assertion in app.main
    # (SPEC-KB-IMAGES-V2-001 REQ-3) AND by the ast-grep guard
    # ``no-hardcoded-kb-image-path.yml`` (REQ-5).
    ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{zitadel_org_id}/images/{kb_slug}/{filename}"
    UPLOAD_ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{kb_slug}"

    # Regex that parses a path produced by :py:meth:`public_path` back into
    # its components. Used by :py:meth:`from_path` AND by the boot-time
    # assertion to verify that ``KbImage(...).public_path`` round-trips.
    _PATH_RE: ClassVar[re.Pattern[str]] = re.compile(
        # Zitadel org segment matches either dev-style alpha-num-dash
        # ("org-1") OR a numeric snowflake. Same alphabet as kb_slug for
        # symmetry; the production tenant boundary is enforced at the auth
        # layer, not by URL parsing.
        r"^/kb-images/(?P<zitadel_org_id>[a-z0-9][a-z0-9-]{0,63}|[0-9]{1,20})"
        r"/images/(?P<kb_slug>[a-z0-9][a-z0-9-]{0,63})"
        r"/(?P<sha256>[0-9a-f]{64})\.(?P<ext>jpg|png|gif|webp)$"
    )

    def __post_init__(self) -> None:
        if not _VALID_ZITADEL_RE.match(self.zitadel_org_id):
            raise ValueError(f"invalid zitadel_org_id: {self.zitadel_org_id!r}")
        if not _VALID_KB_SLUG_RE.match(self.kb_slug):
            raise ValueError(f"invalid kb_slug: {self.kb_slug!r}")
        if not _VALID_SHA256_RE.match(self.sha256):
            raise ValueError(f"invalid sha256: {self.sha256!r}")
        if not _VALID_EXT_RE.match(self.ext):
            raise ValueError(f"invalid ext: {self.ext!r}")

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(
        cls,
        *,
        zitadel_org_id: str,
        kb_slug: str,
        data: bytes,
        mime: str,
    ) -> KbImage:
        """Build a KbImage from the image bytes + tenant scope.

        Raises ``ValueError`` for unsupported MIME or invalid kb_slug /
        zitadel_org_id. SVG is intentionally NOT in ``_MIME_EXT`` — see
        SPEC-PORTAL-DOCS-IMAGE-PASTE-001 REQ-5 for the XSS rationale.
        """
        ext = _MIME_EXT.get(mime)
        if ext is None:
            raise ValueError(f"unsupported MIME for kb-image: {mime!r}")
        return cls(
            zitadel_org_id=zitadel_org_id,
            kb_slug=kb_slug,
            sha256=hashlib.sha256(data).hexdigest(),
            ext=ext,
        )

    @classmethod
    def from_path(cls, path: str) -> KbImage | None:
        """Parse a path produced by :py:meth:`public_path` back into a KbImage.

        Returns ``None`` when ``path`` does not match the canonical shape.
        Used by the boot-time assertion (REQ-3) for round-trip verification
        and by tests that probe the URL shape.
        """
        m = cls._PATH_RE.match(path)
        if m is None:
            return None
        return cls(**m.groupdict())

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @property
    def s3_key(self) -> str:
        """Garage S3 object-key. Wire-level contract since SPEC-KB-IMAGE-002.

        Changing this invalidates every previously uploaded image.
        """
        return f"{self.zitadel_org_id}/images/{self.kb_slug}/{self.sha256}.{self.ext}"

    @property
    def public_path(self) -> str:
        """Relative URL path served by the auth-proxied read-route.

        Always matches ``ROUTE_TEMPLATE`` exactly. A drift would mean
        ``from_path(...).public_path != path`` which the boot-time
        assertion rejects.
        """
        return f"/kb-images/{self.s3_key}"
