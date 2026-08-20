from pathlib import Path

import yaml

TEXT_ALIASES = {"klai-primary", "klai-fast", "klai-large", "klai-medium"}


def test_litellm_tests_run_when_the_runtime_image_pin_changes() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[3] / ".github/workflows/litellm-tests.yml"
    )
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)

    for event in ("push", "pull_request"):
        assert "deploy/docker-compose.yml" in workflow["on"][event]["paths"]


def test_text_aliases_use_mistral_provider_only() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())

    models = {
        alias: [
            entry["litellm_params"]
            for entry in config["model_list"]
            if entry["model_name"] == alias
        ]
        for alias in TEXT_ALIASES
    }

    assert set(models) == TEXT_ALIASES
    for alias, deployments in models.items():
        assert len(deployments) == 2, alias
        deployments_by_order = {params["order"]: params for params in deployments}
        assert set(deployments_by_order) == {1, 2}, alias
        assert deployments_by_order[1]["api_key"] == "os.environ/MISTRAL_API_KEY", alias
        assert (
            deployments_by_order[2]["api_key"] == "os.environ/MISTRAL_API_KEY_BACKUP"
        ), alias
        assert all(params["model"].startswith("mistral/") for params in deployments), (
            alias
        )


def test_litellm_compose_scopes_primary_and_backup_mistral_keys() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    environment = compose["services"]["litellm"]["environment"]

    assert environment["MISTRAL_API_KEY"] == "${MISTRAL_API_KEY}"
    assert environment["MISTRAL_API_KEY_BACKUP"] == "${MISTRAL_API_KEY_BACKUP}"
