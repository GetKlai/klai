"""The delegated-org gate is pinned to LiteLLM's real role constant.

``klai_pii_enforce`` compares ``user_role`` against a hand-written
``"proxy_admin"``. That value was verified once against the installed LiteLLM
and is the only thing between a master-key self-call and an unmasked payload:
if an upgrade renames the role, the comparison silently stops matching, every
internal call goes unattributed again, and no other test notices — they all
spell the string themselves.

The enum is read in a subprocess because sibling test modules install a fake
``litellm`` into ``sys.modules``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_ENFORCER = Path(__file__).parent.parent / "klai_pii_enforce.py"


def _module_constant(name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"$', _ENFORCER.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not found in klai_pii_enforce.py"
    return match.group(1)


def _litellm_admin_roles() -> dict[str, object]:
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;from litellm.proxy._types import LitellmUserRoles as R;"
            'print(json.dumps({"admin": R.PROXY_ADMIN.value,'
            '"all": [r.value for r in R if "admin" in r.value]}))',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_the_role_the_gate_accepts_is_litellms_own_admin_role():
    assert _module_constant("_PROXY_ADMIN_ROLE") == _litellm_admin_roles()["admin"]


def test_the_gate_does_not_accept_the_weaker_admin_roles():
    """`proxy_admin_viewer` and `org_admin` must not be able to name a tenant."""
    accepted = _module_constant("_PROXY_ADMIN_ROLE")
    others = set(_litellm_admin_roles()["all"]) - {accepted}
    assert others, "expected LiteLLM to define more than one admin-ish role"
    assert accepted not in others
