import ast
from pathlib import Path
from urllib.parse import urlparse

import yaml


def _compose() -> dict:
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def _whisper_allowed_hosts() -> set[str]:
    config_path = (
        Path(__file__).resolve().parents[2]
        / "klai-scribe"
        / "scribe-api"
        / "app"
        / "core"
        / "config.py"
    )
    module = ast.parse(config_path.read_text(encoding="utf-8"))

    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "_WHISPER_ALLOWED_HOSTS"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call) or node.value.args == []:
            break
        host_set = node.value.args[0]
        if not isinstance(host_set, (ast.Set, ast.List, ast.Tuple)):
            break
        hosts = set()
        for element in host_set.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                raise AssertionError("_WHISPER_ALLOWED_HOSTS must contain string literals")
            hosts.add(element.value)
        return hosts

    raise AssertionError("_WHISPER_ALLOWED_HOSTS not found in scribe-api config")


def test_scribe_and_vexa_share_transcription_backend_origin():
    compose = _compose()

    scribe_env = compose["services"]["scribe-api"]["environment"]
    meeting_env = compose["services"]["vexa12-meeting-api"]["environment"]

    scribe_base = urlparse(scribe_env["WHISPER_SERVER_URL"])
    meeting_endpoint = urlparse(meeting_env["TRANSCRIPTION_SERVICE_URL"])

    assert meeting_endpoint.path == "/v1/audio/transcriptions"
    assert (meeting_endpoint.scheme, meeting_endpoint.hostname, meeting_endpoint.port) == (
        scribe_base.scheme,
        scribe_base.hostname,
        scribe_base.port,
    )


def test_scribe_compose_transcription_host_is_startup_allowed():
    compose = _compose()

    scribe_env = compose["services"]["scribe-api"]["environment"]
    scribe_base = urlparse(scribe_env["WHISPER_SERVER_URL"])

    assert scribe_base.hostname in _whisper_allowed_hosts()
