"""Guards that litellm and portal-api carry identical citation-rescue config.

klai-libs/citations (klai_citations) is shared by two chat surfaces: the
litellm hook (path A, LibreChat) and portal-api (path B, widget/partner API).
Both read CITATION_RESCUE_MODE, RESCUE_ANSWER_SCORE_THRESHOLD and
RESCUE_RETRIEVAL_RATIO from their own environment, so the two Compose blocks
must carry the same names and the same values (including the ``${VAR:-default}``
default) or the two surfaces resolve rescue behavior differently for
identical KB answers. A comment asking humans to keep the blocks in sync does
not fail a build; this test does.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.yml"

_RESCUE_VARS = (
    "CITATION_RESCUE_MODE",
    "RESCUE_ANSWER_SCORE_THRESHOLD",
    "RESCUE_RETRIEVAL_RATIO",
)


def test_citation_rescue_settings_match_between_litellm_and_portal_api():
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services = compose["services"]
    litellm_env = services["litellm"]["environment"]
    portal_env = services["portal-api"]["environment"]

    for var in _RESCUE_VARS:
        assert var in litellm_env, f"{var} missing from the litellm service environment"
        assert var in portal_env, f"{var} missing from the portal-api service environment"
        assert litellm_env[var] == portal_env[var], (
            f"{var} diverges between litellm ({litellm_env[var]!r}) and "
            f"portal-api ({portal_env[var]!r}); klai_citations is shared by both "
            "chat surfaces and must resolve identically for identical KB answers"
        )
