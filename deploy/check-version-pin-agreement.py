#!/usr/bin/env python3
"""Fail when a version pin that lives in two places has drifted apart.

deploy/docker-compose.yml decides what runs in production; a CI workflow
pin decides what the tests run against. Bump one and forget the other and
CI stays green while testing the old library against the new production.

Add a pair: one PAIRS row, never a second parser. Exit 0 on agreement,
exit 1 on drift, on a missing file, or on a pin line that can no longer
be found — a vanished pin fails loud instead of silently passing.

Usage: python3 deploy/check-version-pin-agreement.py [--self-test] [ROOT]
ROOT overrides the repo root; used by the acceptance test to point the
check at a scratch tree with a deliberately bumped compose pin.
"""
import io
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# name -> one entry per location:
# (path, regex whose only group captures the version, label, template for
#  --self-test trees where {v} is the version)
# Both patterns are anchored to an ACTIVE line, with MULTILINE. A commented
# copy of the pin must not count: a guard that passes while the real pin is
# gone is worse than no guard. The version class admits the whole tag syntax
# (dots, dashes, plus) so `v1.96.2-rc.1` is not silently read as `1.96.2`.
PAIRS = [
    ("litellm", [
        ("deploy/docker-compose.yml",
         r"^[^\S\n]*image:[^\S\n]*ghcr\.io/berriai/litellm:v([\w.+-]+)",
         "compose image",
         "    image: ghcr.io/berriai/litellm:v{v}"),
        (".github/workflows/litellm-tests.yml",
         r"^(?![^\S\n]*#).*--with litellm==([\w.+-]+)",
         "CI uv dependency pin",
         "          --with litellm=={v}"),
    ]),
]


def pin(root, rel, pattern, label):
    """Return (version, None) or (None, error) for one pin location."""
    path = root / rel
    if not path.is_file():
        return None, (f"FAIL: {rel}: file missing — the {label} version pin "
                      f"must live here")
    # Count BEFORE deduplicating: two identical pins is still two places to
    # forget, and set() would report that as one healthy match.
    found = re.findall(pattern, path.read_text(encoding="utf-8"), re.M)
    if len(found) != 1:
        return None, (f"FAIL: {rel}: expected exactly one {label} version pin, "
                      f"found {found or 'none'} — renamed, removed, or duplicated?")
    return found[0], None


def check(root=ROOT):
    ok = True
    for name, spots in PAIRS:
        got = []
        for rel, pattern, label, _tpl in spots:
            version, error = pin(root, rel, pattern, label)
            if error is not None:
                print(error, file=sys.stderr)
                ok = False
            else:
                got.append((rel, version))
        if len({v for _rel, v in got}) > 1:
            ok = False
            print(f"FAIL: {name} version pin disagrees — "
                  + " vs ".join(f"{rel} = {v}" for rel, v in got), file=sys.stderr)
        elif len(got) == len(spots):
            print(f"OK: {name} = {got[0][1]} (in "
                  + " and ".join(rel for rel, _v in got) + ")")
    return ok


def self_test():
    """Agreement, drift, missing file and missing line against synthetic
    trees. The drift case asserts on the first pair (spots[0] vs spots[1])."""
    spots = [s for _name, ss in PAIRS for s in ss]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def run(versions, drop=None, nomatch=None):
            for (rel, _pattern, _label, tpl), v in zip(spots, versions):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(tpl.format(v=v) if rel != nomatch
                                else "# pin moved away\n")
            if drop is not None:
                (root / drop).unlink()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                code = 0 if check(root) else 1
            return code, out.getvalue()

        fails = []
        same = ["1.96.2"] * len(spots)
        code, msg = run(same)
        if code != 0:
            fails.append(f"agreement must exit 0: {msg!r}")
        code, msg = run(["1.97.0", "1.96.2"])
        if code == 0 or not all(s in msg for s in
                                ("1.97.0", "1.96.2", spots[0][0], spots[1][0])):
            fails.append("drift must exit non-zero naming both values and "
                         f"both files: {msg!r}")
        code, msg = run(same, drop=spots[1][0])
        if code == 0 or spots[1][0] not in msg:
            fails.append(f"a missing file must fail loud naming the file: {msg!r}")
        code, msg = run(same, nomatch=spots[0][0])
        if code == 0 or spots[0][0] not in msg:
            fails.append(f"a pin line that is gone must fail loud: {msg!r}")

        # Adversarial fixtures, hand-written rather than generated from the
        # templates: these are the shapes that used to slip through.
        for name, body, why in [
            ("commented out",
             "    # image: ghcr.io/berriai/litellm:v1.96.2\n",
             "a commented-out pin must not count as the pin"),
            ("duplicated",
             "    image: ghcr.io/berriai/litellm:v1.96.2\n"
             "    image: ghcr.io/berriai/litellm:v1.96.2\n",
             "two identical pins are two places to forget, not one match"),
        ]:
            path = root / spots[0][0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            (root / spots[1][0]).parent.mkdir(parents=True, exist_ok=True)
            (root / spots[1][0]).write_text(spots[1][3].format(v="1.96.2"))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                code = 0 if check(root) else 1
            if code == 0:
                fails.append(f"{name}: {why} — got exit 0: {out.getvalue()!r}")

        # A release-candidate tag must be read whole, not truncated.
        code, msg = run(["1.96.2-rc.1", "1.96.2"])
        if code == 0 or "1.96.2-rc.1" not in msg:
            fails.append(f"a -rc tag must be read whole and reported: {msg!r}")

    if fails:
        for failure in fails:
            print("self-test FAIL:", failure, file=sys.stderr)
        return 1
    print("self-test: agreement, drift, missing file, missing line, "
          "commented-out, duplicated, rc-tag — all ok")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--self-test" in argv:
        sys.exit(self_test())
    roots = [a for a in argv if not a.startswith("-")]
    sys.exit(0 if check(Path(roots[0]).resolve() if roots else ROOT) else 1)
