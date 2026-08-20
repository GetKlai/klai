"""Make deploy/presidio/analyzer importable for the test suite.

No network, no Docker needed for this file itself — it only edits sys.path so
`import klai_pii_recognizers` / `import sitecustomize` resolve the same way
they do once copied into the image's site-packages (see
deploy/presidio/analyzer/Dockerfile).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ANALYZER_DIR = Path(__file__).resolve().parent.parent / "analyzer"
if str(_ANALYZER_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYZER_DIR))
