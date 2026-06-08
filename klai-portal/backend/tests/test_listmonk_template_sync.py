from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_sync_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "deploy" / "scripts" / "listmonk-sync-templates.py"
    spec = importlib.util.spec_from_file_location("listmonk_sync_templates", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_campaign_template_uses_klai_shell_and_content_slot() -> None:
    module = _load_sync_module()

    payload = module.build_payload(module.TEMPLATES_BY_SLUG["campaign"])

    assert payload["name"] == "Klai branded campaign"
    assert payload["type"] == "campaign"
    assert "subject" not in payload
    assert "https://getklai.com/logo-black.svg" in payload["body"]
    assert "https://getklai.com/klai-logo.png" not in payload["body"]
    assert payload["body"].count('{{ template "content" . }}') == 1
    assert "{{ UnsubscribeURL }}" in payload["body"]
    assert "{{ TrackView }}" in payload["body"]


def test_onboarding_tx_template_uses_waitlist_copy_and_tx_data() -> None:
    module = _load_sync_module()

    payload = module.build_payload(module.TEMPLATES_BY_SLUG["onboarding_invite"])

    assert payload["name"] == "Klai onboarding invite"
    assert payload["type"] == "tx"
    assert payload["subject"] == "Welcome to Klai, you're in"
    assert "https://getklai.com/logo-black.svg" in payload["body"]
    assert "https://getklai.com/founders.jpg" in payload["body"]
    assert "{{ .Tx.Data.name }}" in payload["body"]
    assert "{{ .Tx.Data.cal_url }}" in payload["body"]
    assert "By joining our waitlist" in payload["body"]


def test_sync_updates_existing_template_by_name(monkeypatch) -> None:
    module = _load_sync_module()
    monkeypatch.delenv("LISTMONK_TEMPLATE_KLAI_CAMPAIGN_ID", raising=False)

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

        def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append((method, path, payload))
            if method == "GET":
                return {"data": [{"id": 7, "name": "Klai branded campaign", "type": "campaign"}]}
            return {"data": {"id": 7}}

    client = FakeClient()

    result = module.sync_template(client, module.TEMPLATES_BY_SLUG["campaign"])

    assert result.action == "updated"
    assert result.template_id == 7
    assert client.calls[0] == ("GET", "/api/templates", None)
    method, path, payload = client.calls[1]
    assert method == "PUT"
    assert path == "/api/templates/7"
    assert payload is not None
    assert payload["type"] == "campaign"
    assert payload["body"].count('{{ template "content" . }}') == 1
