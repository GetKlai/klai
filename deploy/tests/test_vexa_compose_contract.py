from pathlib import Path

import yaml


def test_meeting_api_receives_internal_callback_secret():
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    meeting_env = compose["services"]["meeting-api"]["environment"]

    assert meeting_env["INTERNAL_API_SECRET"] == "${INTERNAL_API_SECRET}"
