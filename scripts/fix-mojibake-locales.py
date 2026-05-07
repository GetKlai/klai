#!/usr/bin/env python3
"""
Fix UTF-8 → CP1252 → UTF-8 double-encoded mojibake in locale JSON files.

Background
----------
At some point in the past, klai-portal/frontend/messages/{en,nl}.json were read
as Windows-1252 and re-saved as UTF-8. That bakes patterns like "â€¦" (instead
of "…"), "Ã©" (instead of "é"), "Â·" (instead of "·") permanently into the
bytes on disk. The corruption is exactly reversible with:

    s.encode('cp1252').decode('utf-8')

CP1252 (not strict Latin-1) is the right reverse: characters like €, –, —, …,
', ", •, ™ live in the CP1252 0x80-0x9F range and would fail under Latin-1.

This script applies that reversal to every string value in the target JSON
files, but only when:

  1. The reversal succeeds (string IS cp1252-representable AND the resulting
     bytes ARE valid UTF-8), AND
  2. The reversed string differs from the original, AND
  3. The original contains at least one known mojibake byte sequence.

That triple-gate makes the script idempotent: a clean string is never touched,
so re-running on already-fixed files is a no-op.

Usage
-----
    python3 scripts/fix-mojibake-locales.py            # apply fix
    python3 scripts/fix-mojibake-locales.py --check    # exit 1 if mojibake
                                                       #   would be changed
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIRS = [
    REPO_ROOT / "klai-portal" / "frontend" / "messages",
]


def discover_targets() -> list[Path]:
    """Find every locale JSON file in known locale dirs. Auto-picks up new
    locales (e.g. fr.json, de.json) without requiring a script change."""
    targets: list[Path] = []
    for locale_dir in LOCALE_DIRS:
        if locale_dir.is_dir():
            targets.extend(sorted(locale_dir.glob("*.json")))
    return targets

# Byte sequences that only show up via the cp1252-as-utf8 corruption pattern.
# We require at least one of these in the original string before we attempt to
# rewrite, so plain ASCII or already-correct strings are never touched.
#
# `â€` covers â€¦ (…), â€" (—, where " is U+201D from cp1252 0x94),
# â€" (–, U+201C from 0x93), â€™ (’, U+2122 from 0x99), …
# `Ã[non-ASCII]` covers Ã© (é), Ã« (ë), Ã¨ (è), Ã¯ (ï), …
# `Â[non-ASCII]` covers Â· (·), Â± (±), Â  (NBSP), …
MOJIBAKE_SIGNATURES = re.compile(
    r"â€"
    r"|Ã[^\x00-\x7f]"
    r"|Â[^\x00-\x7f]"
)


def attempt_fix(value: str) -> str | None:
    """
    Return the de-mojibaked string, or None if `value` is not mojibake.
    """
    if not MOJIBAKE_SIGNATURES.search(value):
        return None
    try:
        repaired = value.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if repaired == value:
        return None
    return repaired


def fix_obj(obj, changes: list[tuple[str, str, str]], path: str = ""):
    """
    Walk a JSON value tree, fixing strings in place. Records every change as
    (json_path, before, after) for human review.
    """
    if isinstance(obj, dict):
        for key, val in obj.items():
            sub_path = f"{path}.{key}" if path else key
            if isinstance(val, str):
                fixed = attempt_fix(val)
                if fixed is not None:
                    obj[key] = fixed
                    changes.append((sub_path, val, fixed))
            else:
                fix_obj(val, changes, sub_path)
    elif isinstance(obj, list):
        for index, val in enumerate(obj):
            sub_path = f"{path}[{index}]"
            if isinstance(val, str):
                fixed = attempt_fix(val)
                if fixed is not None:
                    obj[index] = fixed
                    changes.append((sub_path, val, fixed))
            else:
                fix_obj(val, changes, sub_path)


def process_file(path: Path, write: bool) -> list[tuple[str, str, str]]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    changes: list[tuple[str, str, str]] = []
    fix_obj(data, changes)
    if changes and write:
        # ensure_ascii=False keeps real UTF-8 in the file; indent=2 matches
        # the existing formatting of the locale JSONs.
        new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        path.write_text(new_text, encoding="utf-8")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; exit 1 if any file would be changed.",
    )
    args = parser.parse_args()

    targets = discover_targets()
    if not targets:
        print("warning: no locale files discovered", file=sys.stderr)
        return 0

    total_changes = 0
    for target in targets:
        changes = process_file(target, write=not args.check)
        rel = target.relative_to(REPO_ROOT)
        if changes:
            verb = "would fix" if args.check else "fixed"
            print(f"{rel}: {verb} {len(changes)} string(s)")
            for json_path, before, after in changes:
                print(f"  {json_path}")
                print(f"    -  {before!r}")
                print(f"    +  {after!r}")
            total_changes += len(changes)
        else:
            print(f"{rel}: clean")

    if args.check and total_changes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
