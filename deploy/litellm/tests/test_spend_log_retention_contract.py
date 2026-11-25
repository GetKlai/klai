"""The spend-log retention settings in config.yaml must exist in the pinned gateway.

LiteLLM ignores a general_settings key it does not know, so a bump that renames
one of these would stop the cleanup without an error and the table would grow
unbounded again (7.1 GB on 2026-11-25). Verified against the installed source of
v1.109.1; the package source is read as text in a subprocess because sibling
tests put a fake ``litellm`` into ``sys.modules``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"
_PROXY_SOURCE = (
    "import importlib.util, pathlib;"
    "print((pathlib.Path(importlib.util.find_spec('litellm').origin).parent"
    " / 'proxy' / 'proxy_server.py').read_text())"
)


def test_spend_log_retention_settings_exist_in_the_pinned_gateway() -> None:
    settings = yaml.safe_load(_CONFIG.read_text())["general_settings"]
    ours = sorted(key for key in settings if key.startswith("maximum_spend_logs_"))
    assert ours == ["maximum_spend_logs_cleanup_cron", "maximum_spend_logs_retention_period"]

    source = subprocess.run(
        [sys.executable, "-c", _PROXY_SOURCE], capture_output=True, text=True, check=True
    ).stdout
    assert [key for key in ours if f'"{key}"' not in source] == []
