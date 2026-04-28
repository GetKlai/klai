"""Extract secret-shaped field values from a Settings-like object.

SPEC-SEC-INTERNAL-001 REQ-4.2.
"""

from __future__ import annotations

import re
from typing import Any

# Field-name regex per REQ-4.2: secret/password/token/pat/api_key.
# Case-insensitive. ``pat`` matches both ``personal_access_token`` and
# ``github_app_pat``-style names.
_SECRET_NAME_RE = re.compile(r"(?i)(secret|password|token|pat|api_key)")
_MIN_VALUE_LENGTH = 8  # shorter values are too generic to safely scrub


def extract_secret_values(settings_obj: object) -> set[str]:
    """Return the set of non-empty secret-shaped string values on ``settings_obj``.

    Walks a Pydantic-Settings instance (``model_fields``) or a plain
    attribute object. Field-name match uses ``_SECRET_NAME_RE``. Values
    shorter than ``_MIN_VALUE_LENGTH`` are skipped to prevent
    over-redaction of common substrings.
    """
    if settings_obj is None:
        return set()

    field_names = _enumerate_field_names(settings_obj)
    values: set[str] = set()
    for name in field_names:
        if not _SECRET_NAME_RE.search(name):
            continue
        try:
            raw = getattr(settings_obj, name)
        except AttributeError:
            continue
        if isinstance(raw, str) and len(raw) >= _MIN_VALUE_LENGTH:
            values.add(raw)
    return values


def _enumerate_field_names(obj: object) -> list[str]:
    """Best-effort field enumeration covering pydantic + plain objects.

    Pydantic v2.11 deprecates instance-level ``model_fields`` access in
    favour of ``type(obj).model_fields``. We try the class first and fall
    back to the instance attribute so this works against pre-2.11 codebases
    AND plain (non-pydantic) namespaces.
    """
    # Pydantic v2: BaseSettings.model_fields is a dict[str, FieldInfo] on the class.
    model_fields: Any = getattr(type(obj), "model_fields", None)
    if not isinstance(model_fields, dict):
        # Pre-2.11 compatibility: instance-level access still works.
        model_fields = getattr(obj, "model_fields", None)

    if isinstance(model_fields, dict):
        keys: list[str] = []
        for raw_key in model_fields:  # type: ignore[reportUnknownVariableType]
            if isinstance(raw_key, str):
                keys.append(raw_key)
        return keys

    # Plain object -- fall back to public attributes (skip dunders + callables).
    return [
        name
        for name in dir(obj)
        if not name.startswith("_") and not callable(getattr(obj, name, None))
    ]
