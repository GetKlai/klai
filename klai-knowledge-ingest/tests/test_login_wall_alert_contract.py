"""The login-wall alert rules must query log events that knowledge-ingest emits.

On 2026-08-18 the crawler's wall events were renamed from ``login_wall_*`` to
``content_wall_signal_*`` while ``deploy/grafana/.../login-wall-rules.yaml``
kept querying the old names. The burst alert matched nothing from then on,
so weeks of rejected crawls well above its threshold never fired it.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_RULES = _REPO_ROOT / "deploy/grafana/provisioning/alerting/login-wall-rules.yaml"
_SOURCE = Path(__file__).parent.parent / "knowledge_ingest"


def test_every_alerted_event_is_emitted_by_knowledge_ingest() -> None:
    exprs = re.findall(r"^\s*expr: '([^']*)'", _RULES.read_text(), flags=re.MULTILINE)
    events = {e for expr in exprs for e in re.findall(r"event:(\w+)", expr)}
    source = "\n".join(p.read_text() for p in _SOURCE.rglob("*.py"))
    # Only a logger call counts: the old names still appear in comments.
    emitted = set(re.findall(r'logger\.\w+\(\s*"(\w+)"', source))

    assert events
    missing = sorted(events - emitted)
    assert missing == []
