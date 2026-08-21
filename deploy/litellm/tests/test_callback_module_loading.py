"""Every klai_* callback module must import the way LiteLLM loads it.

Production incident 2026-08-21: `klai_pii_enforce.py` passed all 837 unit
tests and then crashlooped litellm on startup with

    AttributeError: 'NoneType' object has no attribute '__dict__'

at a module-level `@dataclass`. The cause is how LiteLLM loads callbacks
(`litellm/proxy/types_utils/utils.py::get_instance_fn`):

    spec   = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # <- never inserted into sys.modules

`exec_module` runs the module body while `sys.modules[name]` does not
exist. With `from __future__ import annotations` every annotation is a
string, so `dataclasses._process_class` calls `_is_type`, which does
`sys.modules.get(cls.__module__).__dict__` — and that is `None`.

pytest imports these modules normally, so `sys.modules` IS populated and
the whole class of bug is invisible to every other test in this suite.
This module closes that gap by using the real loader.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_LITELLM_DIR = Path(__file__).resolve().parent.parent

def _registered_callback_modules() -> list[str]:
    """Module names from config.yaml's `litellm_settings.callbacks`.

    Scope matters and was measured on the running container: ONLY modules
    named in `callbacks:` go through `get_instance_fn`'s file-path loader.
    Everything else is pulled in by a normal `import` from one of those, at
    which point it IS in sys.modules and the dataclass path is fine. Seven
    sibling modules fail this loader today and are perfectly healthy in
    production for exactly that reason — so globbing `klai_*.py` would
    produce noise, not signal.

    Read from config.yaml rather than hard-coded, so registering a new
    callback puts it under this test on the same commit.
    """
    import yaml

    config = yaml.safe_load((_LITELLM_DIR / "config.yaml").read_text())
    entries = (config.get("litellm_settings") or {}).get("callbacks") or []
    names = []
    for entry in entries:
        if isinstance(entry, str) and "." in entry:
            names.append(entry.rsplit(".", 1)[0])
    return sorted(set(names))


_CALLBACK_MODULES = _registered_callback_modules()


# Two snippets, identical except for ONE line. `sys.modules[name] = module`
# is present in the "normal" variant and absent in the "loader" variant —
# that single omission is the entire difference between a healthy import and
# the production crashloop, so the test isolates exactly it.
_SNIPPET = """
import importlib.util, os, sys
os.chdir({dir!r})
sys.path.insert(0, {dir!r})
spec = importlib.util.spec_from_file_location({name!r}, {path!r})
module = importlib.util.module_from_spec(spec)
{register}
spec.loader.exec_module(module)
"""
_REGISTER = "sys.modules[{name!r}] = module"


def _run_load(module_name: str, module_path: Path, *, register: bool):
    """Load in a FRESH subprocess, the way container startup does.

    In-process loading is order-dependent — pytest has already imported most
    of these modules, so the outcome depends on which sibling populated
    sys.modules first. A clean interpreter per attempt is the only way to
    get a deterministic answer, and it mirrors deploy: one process, each
    callback loaded once.
    """
    code = _SNIPPET.format(
        dir=str(_LITELLM_DIR),
        name=module_name,
        path=str(module_path),
        register=_REGISTER.format(name=module_name) if register else "",
    )
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )


def test_callbacks_are_discovered_from_config():
    """Guard against the parser silently yielding nothing."""
    assert _CALLBACK_MODULES, "no callbacks parsed from config.yaml"
    assert "klai_knowledge" in _CALLBACK_MODULES


@pytest.mark.parametrize("module_name", _CALLBACK_MODULES)
def test_module_imports_under_litellms_loader(module_name):
    """Import must succeed with the module absent from sys.modules.

    A failure here means litellm will crashloop on deploy, regardless of
    how many normal-import tests pass.
    """
    module_path = _LITELLM_DIR / f"{module_name}.py"
    if not module_path.exists():
        pytest.skip(f"{module_name} is not a local module in this directory")
    normal = _run_load(module_name, module_path, register=True)
    if normal.returncode != 0:
        # Fails either way — missing env var, absent dependency, etc. Not a
        # loader-specific defect, and container startup surfaces it anyway.
        pytest.skip(
            f"{module_name} does not import in this environment at all "
            f"(so the loader variant proves nothing): "
            f"{normal.stderr.strip().splitlines()[-1][:160]}"
        )

    loader = _run_load(module_name, module_path, register=False)
    assert loader.returncode == 0, (
        f"{module_name} imports normally but FAILS under LiteLLM's loader. "
        f"This crashloops the proxy on deploy and no other test in this "
        f"suite can see it:\n{loader.stderr[-2000:]}"
    )
