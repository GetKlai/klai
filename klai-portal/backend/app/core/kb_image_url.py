"""Re-export shim — the actual single-source-of-truth is
``klai_image_storage.kb_image.KbImage``.

SPEC-KB-IMAGES-V2-001 REQ-1: ``KbImage`` lives in ``klai-libs/image-storage``
so that klai-portal AND the connector + knowledge-ingest pipelines all
import from the exact same value-class. ``app/core/kb_image_url.py`` is
the portal-side import path because that's where klai-portal handlers
look for core types; it forwards to the lib without adding logic.

Any change to URL shape, S3-key format, MIME table, or route templates
goes in ``klai_image_storage/kb_image.py`` and is automatically picked
up here.
"""

from klai_image_storage.kb_image import KbImage

__all__ = ["KbImage"]
