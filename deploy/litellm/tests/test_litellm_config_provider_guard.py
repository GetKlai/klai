from pathlib import Path

import yaml


TEXT_ALIASES = {"klai-primary", "klai-fast", "klai-large", "klai-medium"}


def test_text_aliases_use_mistral_provider_only() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    config = yaml.safe_load(config_path.read_text())

    models = {
        entry["model_name"]: entry["litellm_params"]
        for entry in config["model_list"]
        if entry["model_name"] in TEXT_ALIASES
    }

    assert set(models) == TEXT_ALIASES
    for alias, params in models.items():
        assert params["model"].startswith("mistral/"), alias
        assert params["api_key"] == "os.environ/MISTRAL_API_KEY", alias
