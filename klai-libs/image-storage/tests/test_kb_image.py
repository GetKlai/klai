"""Unit tests for the KbImage single-source-of-truth value-class.

SPEC-KB-IMAGES-V2-001 AC-1 + AC-3 (drift-aborts-boot).
"""

from __future__ import annotations

import hashlib

import pytest

from klai_image_storage.kb_image import KbImage

# ---------------------------------------------------------------------------
# AC-1: round-trip on real production keys
# ---------------------------------------------------------------------------

# These are actual s3_keys queried from knowledge.artifact_images on
# 2026-05-12 (Voys org id=8 / zitadel=368884765035593759). The test fixture
# is a stable subset that proves KbImage.from_path + .public_path round-trip
# byte-for-byte against what the connector pipeline writes.
_PRODUCTION_PATHS = [
    "/kb-images/368884765035593759/images/support/dae543ab51b40c9611d14c96e1f72bbd53a1ecdc782c192fbc2ab6d0e6127dd9.png",
    "/kb-images/368884765035593759/images/support/d4818c6438a7c33935b06fa8c66387cfdb8418ed8f2ecae7c8f47fdf5712a789.png",
    "/kb-images/368884765035593759/images/support/49a861d9954135083d4b0bb9417565ca92e5ad477f64d6cf0dcba17d20077aa9.png",
    # Klai-help org (1 / zitadel 362757920133283846) — Mark's docs-editor uploads
    "/kb-images/362757920133283846/images/klai-help/71e67cdc4b7451885b314b848583f5a66838b1952d753f5d133b5d98dc375f5b.png",
    "/kb-images/362757920133283846/images/klai-help/ebe2b85db57d52f3470b35cb47b5f0c3e7114821f49d27d35c0e6cb1195ca605.png",
]


@pytest.mark.parametrize("path", _PRODUCTION_PATHS)
def test_round_trip_production_paths(path: str) -> None:
    """AC-1: every real production URL parses + serializes back identically."""
    parsed = KbImage.from_path(path)
    assert parsed is not None, f"from_path returned None for {path!r}"
    assert parsed.public_path == path, "public_path drifted from input"
    # s3_key is public_path minus the /kb-images/ prefix
    assert parsed.s3_key == path.removeprefix("/kb-images/")


def test_s3_key_shape_unchanged_from_speck_b_image_002() -> None:
    """The S3 key prefix is a wire-level contract. Changing it breaks every
    previously uploaded image's URL. Lock the shape down in a test."""
    img = KbImage(
        zitadel_org_id="368884765035593759",
        kb_slug="support",
        sha256="a" * 64,
        ext="png",
    )
    assert img.s3_key == f"368884765035593759/images/support/{'a' * 64}.png"
    assert img.public_path == f"/kb-images/368884765035593759/images/support/{'a' * 64}.png"


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def test_from_bytes_hashes_with_sha256() -> None:
    """SHA-256 dedup is the storage layer's only invariant; lock it."""
    data = b"hello world"
    img = KbImage.from_bytes(
        zitadel_org_id="368884765035593759",
        kb_slug="support",
        data=data,
        mime="image/png",
    )
    assert img.sha256 == hashlib.sha256(data).hexdigest()
    assert img.ext == "png"


def test_from_bytes_rejects_svg() -> None:
    """SPEC-PORTAL-DOCS-IMAGE-PASTE-001 REQ-5: SVG XSS guard for user uploads."""
    with pytest.raises(ValueError, match="unsupported MIME"):
        KbImage.from_bytes(
            zitadel_org_id="368884765035593759",
            kb_slug="support",
            data=b"<svg/>",
            mime="image/svg+xml",
        )


def test_from_bytes_rejects_arbitrary_mime() -> None:
    with pytest.raises(ValueError, match="unsupported MIME"):
        KbImage.from_bytes(
            zitadel_org_id="368884765035593759",
            kb_slug="support",
            data=b"\x00" * 32,
            mime="application/pdf",
        )


@pytest.mark.parametrize(
    "mime, expected_ext",
    [
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/gif", "gif"),
        ("image/webp", "webp"),
    ],
)
def test_from_bytes_mime_to_ext_table(mime: str, expected_ext: str) -> None:
    img = KbImage.from_bytes(
        zitadel_org_id="368884765035593759",
        kb_slug="support",
        data=b"\x00" * 32,
        mime=mime,
    )
    assert img.ext == expected_ext


def test_from_path_rejects_garbage() -> None:
    """Defense: from_path returns None for anything that doesn't match the
    canonical 5-segment shape. Used by the boot-time assertion to catch
    route-template drift."""
    bad_paths = [
        "/kb-images/foo/bar/baz.png",  # 3 segments
        "/kb-images/368884765035593759/support/abc.png",  # 4 segments (old shape)
        "/kb-images/368884765035593759/images/support/abc.png",  # short sha
        "/kb-images/368884765035593759/imgs/support/" + "a" * 64 + ".png",  # wrong literal
        "/wrong-prefix/368884765035593759/images/support/" + "a" * 64 + ".png",
        "/kb-images/368884765035593759/images/Bad-SLUG/" + "a" * 64 + ".png",  # caps in slug
        "/kb-images/368884765035593759/images/support/" + "a" * 64 + ".svg",  # disallowed ext
    ]
    for p in bad_paths:
        assert KbImage.from_path(p) is None, f"from_path should have rejected {p!r}"


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


def test_invalid_zitadel_org_id() -> None:
    with pytest.raises(ValueError, match="zitadel_org_id"):
        KbImage(zitadel_org_id="ORG-WITH-CAPS", kb_slug="support", sha256="a" * 64, ext="png")


def test_invalid_kb_slug() -> None:
    with pytest.raises(ValueError, match="kb_slug"):
        KbImage(zitadel_org_id="42", kb_slug="-bad-start", sha256="a" * 64, ext="png")


def test_invalid_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        KbImage(zitadel_org_id="42", kb_slug="support", sha256="zz", ext="png")


def test_invalid_ext() -> None:
    with pytest.raises(ValueError, match="ext"):
        KbImage(zitadel_org_id="42", kb_slug="support", sha256="a" * 64, ext="svg")


# ---------------------------------------------------------------------------
# Route templates exposed for use by FastAPI decorators
# ---------------------------------------------------------------------------


def test_route_templates_are_strings_not_optionals() -> None:
    assert isinstance(KbImage.ROUTE_TEMPLATE, str)
    assert isinstance(KbImage.UPLOAD_ROUTE_TEMPLATE, str)
    assert KbImage.ROUTE_TEMPLATE.startswith("/kb-images/")
    assert KbImage.UPLOAD_ROUTE_TEMPLATE.startswith("/kb-images/")


def test_route_template_round_trips_via_concrete_instance() -> None:
    """The route template's placeholders must match exactly what
    public_path() generates for a concrete KbImage. This is the assertion
    that the boot-time check in main.py reproduces — a drift here means
    portal-api refuses to boot."""
    concrete = KbImage(
        zitadel_org_id="368884765035593759",
        kb_slug="support",
        sha256="a" * 64,
        ext="png",
    )
    # The template, when formatted with the concrete instance's fields,
    # must produce public_path() verbatim.
    rendered = KbImage.ROUTE_TEMPLATE.format(
        zitadel_org_id=concrete.zitadel_org_id,
        kb_slug=concrete.kb_slug,
        filename=f"{concrete.sha256}.{concrete.ext}",
    )
    assert rendered == concrete.public_path


# ---------------------------------------------------------------------------
# Cross-class drift: ImageStore.build_object_key must agree with KbImage.s3_key
# ---------------------------------------------------------------------------

# SPEC-KB-IMAGES-V2-FOLLOWUPS-001: the runtime drift-check that compared
# ``result.object_key`` against ``KbImage(...).s3_key`` in the route + pipeline
# is replaced by this unit test. As long as this test passes, ImageStore and
# KbImage produce byte-identical S3 keys for the same inputs and the runtime
# tautology can stay deleted.


def test_image_store_build_object_key_matches_kb_image_s3_key() -> None:
    """Lock the wire-level invariant: ImageStore.build_object_key and
    KbImage.s3_key produce byte-identical S3 keys for the same inputs.

    Replaces the per-upload runtime tautology check in the POST route +
    pipeline.py. If this test ever fails, restore the runtime guards.
    """
    from klai_image_storage.storage import ImageStore

    payloads = [
        b"hello world",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 200,
        b"GIF89a" + b"\x00" * 50,
    ]
    org_ids = ["368884765035593759", "362757920133283846", "100000000000000001"]
    kb_slugs = ["support", "klai-help", "my-kb"]
    mimes_and_exts = [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp")]

    for data in payloads:
        for org in org_ids:
            for kb in kb_slugs:
                for mime, ext in mimes_and_exts:
                    kb_image = KbImage.from_bytes(
                        zitadel_org_id=org,
                        kb_slug=kb,
                        data=data,
                        mime=mime,
                    )
                    store_key = ImageStore.build_object_key(org, kb, data, ext)
                    assert store_key == kb_image.s3_key, (
                        f"drift: ImageStore.build_object_key={store_key!r} "
                        f"vs KbImage.s3_key={kb_image.s3_key!r} "
                        f"(org={org}, kb={kb}, mime={mime})"
                    )
