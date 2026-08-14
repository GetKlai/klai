from pathlib import Path

import yaml


def test_meeting_api_receives_internal_callback_secret():
    """The bot's lifecycle callback to meeting-api is authenticated by this secret.

    Renamed from INTERNAL_API_SECRET to VEXA12_INTERNAL_SECRET in SPEC-VEXA-004: the
    Vexa stack is its own trust boundary and must not share a secret with the rest of
    klai. Enforced from the other side by
    klai-portal/backend/tests/contract::test_vexa_secrets_are_dedicated_to_the_vexa_stack.
    """
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    meeting_env = compose["services"]["vexa12-meeting-api"]["environment"]

    assert meeting_env["INTERNAL_API_SECRET"] == "${VEXA12_INTERNAL_SECRET}"
