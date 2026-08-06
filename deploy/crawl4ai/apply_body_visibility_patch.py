"""Patch crawl4ai's hardcoded 30s body-visibility wait to be env-tunable.

Why this exists: crawl4ai waits up to 30s for document.body to become
visible, then discards the result because ignore_body_visibility defaults to
True (async_crawler_strategy.py, the csp_compliant_wait call). On pages
whose body never becomes visible — AngularJS ng-cloak / Vue v-cloak sites
where the app fails to bootstrap — every crawl pays the full 30s for a value
nobody reads. Measured on support.ascendcloud.com: 34.7s per page of which
30.1s is this single wait; with the wait capped at 2s the output is
byte-identical in 6.5s.

Reported upstream with a config-field proposal:
https://github.com/unclecode/crawl4ai/issues/2129

This image-build patch is the minimal deployment variant of that proposal:
one call site changes from a hardcoded 30000 to an env-var lookup
(CRAWL4AI_BODY_VISIBILITY_TIMEOUT, default 30000 = stock behaviour). Set
the env var in docker-compose.yml to tune it without rebuilding. Drop this
patch when upstream ships a configurable timeout.

Fails the build loudly when the anchor is missing or ambiguous, so a
crawl4ai base-image bump can never silently ship an unpatched image.
"""

import pathlib
import sys

import crawl4ai

STRATEGY = pathlib.Path(crawl4ai.__file__).parent / "async_crawler_strategy.py"

ANCHOR = (
    'return isVisible;\n'
    '                    }""",\n'
    '                    timeout=30000,\n'
    '                )'
)
REPLACEMENT = (
    'return isVisible;\n'
    '                    }""",\n'
    '                    timeout=int(os.environ.get('
    '"CRAWL4AI_BODY_VISIBILITY_TIMEOUT", "30000")),\n'
    '                )'
)

text = STRATEGY.read_text()
count = text.count(ANCHOR)
if count != 1:
    sys.exit(
        f"REFUSING TO BUILD: body-visibility anchor found {count}x in "
        f"{STRATEGY} (expected exactly 1). The crawl4ai base image changed; "
        "re-verify the patch against the new source."
    )
if "import os" not in text.split("\n\n")[0] and "\nimport os\n" not in text:
    sys.exit(f"REFUSING TO BUILD: 'import os' not found in {STRATEGY}.")

STRATEGY.write_text(text.replace(ANCHOR, REPLACEMENT, 1))

# Read back and verify — do not trust the write.
patched = STRATEGY.read_text()
if 'CRAWL4AI_BODY_VISIBILITY_TIMEOUT' not in patched or ANCHOR in patched:
    sys.exit("REFUSING TO BUILD: patch verification failed after write.")
print(f"patched OK: {STRATEGY}")
