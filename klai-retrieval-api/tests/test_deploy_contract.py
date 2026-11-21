from pathlib import Path

import yaml

from retrieval_api.config import Settings

# Read from the process environment by a dependency, not by Settings:
# graphiti-core's default cross-encoder builds an OpenAI client at start-up.
_READ_BY_DEPENDENCY = {"OPENAI_API_KEY"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_retrieval_api_image_copies_all_path_dependencies_before_install():
    dockerfile = (_repo_root() / "klai-retrieval-api/Dockerfile").read_text()

    install_index = dockerfile.index("RUN uv pip install --system -e .")
    for path_dep in (
        "klai-libs/identity-assert",
        "klai-libs/chat-prompts",
        "klai-libs/citations",
    ):
        copy_index = dockerfile.index(f"COPY --chown=app:app {path_dep} {path_dep}")
        assert copy_index < install_index


def test_retrieval_api_workflow_rebuilds_when_path_dependencies_change():
    workflow = (_repo_root() / ".github/workflows/retrieval-api.yml").read_text()

    for path_dep in (
        "klai-libs/identity-assert/**",
        "klai-libs/chat-prompts/**",
        "klai-libs/citations/**",
    ):
        assert path_dep in workflow


def test_compose_sets_only_variables_the_service_reads():
    compose = yaml.safe_load((_repo_root() / "deploy/docker-compose.yml").read_text())
    environment = compose["services"]["retrieval-api"]["environment"]

    unread = [
        name
        for name in environment
        if name.lower() not in Settings.model_fields and name not in _READ_BY_DEPENDENCY
    ]

    assert unread == []
