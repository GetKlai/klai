#!/usr/bin/env python3
"""Render the per-tenant librechat.yaml the way portal-api does.

usage: render-tenant-librechat-yaml.py <base-librechat.yaml> [mcp-server-id ...]

Runs the real _generate_librechat_yaml from
klai-portal/backend/app/services/provisioning/generators.py, with the MCP
catalog next to the base file, and prints the result. Each id given is
rendered as an enabled tenant MCP server. Nothing here is tenant data.

generators.py imports the portal settings, structlog and the secrets service at
module level; none of them is used by this function, so they are replaced by
empty stand-ins and the portal's dependencies are not installed just to render
a YAML file. Needs only PyYAML.
"""

import importlib.util
import sys
import types
from pathlib import Path

GENERATORS = (
    Path(__file__).resolve().parents[2]
    / "klai-portal/backend/app/services/provisioning/generators.py"
)


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        print(f"warning: {args} {kwargs}", file=sys.stderr)


_stub("structlog", get_logger=lambda: _Logger())
_stub("app")
_stub("app.core")
_stub("app.core.config", settings=None)
_stub("app.core.provisioning_names", validate_slug_for_provisioning=None)
_stub("app.services")
_stub("app.services.secrets", decrypt_mcp_secret=None, is_secret_var=None)

spec = importlib.util.spec_from_file_location("generators_under_render", GENERATORS)
generators = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generators)

base = Path(sys.argv[1])
servers = {server_id: {"enabled": True} for server_id in sys.argv[2:]}
sys.stdout.write(generators._generate_librechat_yaml(base, servers))
