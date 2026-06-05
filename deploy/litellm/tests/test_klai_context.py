import importlib
import sys


def _load_context(monkeypatch, extra_env=None):
    env = {
        "KLAI_CONTEXT_PRIMARY_HISTORY_BUDGET_CHARS": "1000",
        "KLAI_CONTEXT_FAST_HISTORY_BUDGET_CHARS": "800",
        "KLAI_CONTEXT_LARGE_HISTORY_BUDGET_CHARS": "2000",
    }
    if extra_env:
        env.update(extra_env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("klai_context", None)
    import klai_context

    importlib.reload(klai_context)
    return klai_context


def test_orchestrator_exposes_mistral_model_profile_metadata(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [{"role": "user", "content": "Hallo"}],
        requested_model="klai-large",
    )

    meta = result.meta
    assert meta["orchestrator"] == "klai_context"
    assert meta["provider"] == "mistral"
    assert meta["requested_model"] == "klai-large"
    assert meta["profile_selection_phase"] == "pre_router_litellm_callback"
    assert meta["model_profile"] == "klai-large"
    assert meta["upstream_model"] == "mistral-large-2512"
    assert meta["history_budget_chars"] == 2000
    assert meta["output_reserve_chars"] == 48000


def test_orchestrator_normalizes_text_parts_and_omits_stale_upload(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Attached document(s):\nold body"}
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "Oud antwoord"}]},
            {"role": "user", "content": [{"type": "text", "text": "Nieuwe vraag"}]},
        ],
        requested_model="klai-primary",
    )

    assert result.messages[-1]["content"] == "Nieuwe vraag"
    assert result.messages[0]["content"] == mod.STALE_ATTACHMENT_CONTEXT_PLACEHOLDER
    assert result.messages[1]["content"] == "Oud antwoord"
    assert result.meta["normalized_text_part_messages"] == 3
    assert result.meta["normalized_user_text_part_messages"] == 2
    assert result.meta["stale_attachment_placeholders"] == 1


def test_orchestrator_budget_omits_old_history_but_keeps_latest_user(monkeypatch):
    mod = _load_context(monkeypatch, {"KLAI_CONTEXT_HISTORY_BUDGET_CHARS": "40"})
    orchestrator = mod.KlaiContextOrchestrator()
    latest = "Keep this exact latest user message"

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "old user " + ("x" * 80)},
            {"role": "assistant", "content": "old assistant " + ("y" * 80)},
            {"role": "user", "content": latest},
        ],
        requested_model="klai-primary",
    )

    rendered = "\n".join(
        message.get("content", "")
        for message in result.messages
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )
    assert latest in rendered
    assert "old user" not in rendered
    assert "old assistant" not in rendered
    assert mod.HISTORY_BUDGET_CONTEXT_PLACEHOLDER in rendered
    assert result.meta["omitted_history_messages"] == 2
    assert "history_budget_exceeded" in result.meta["reason_codes"]


def test_orchestrator_omits_contiguous_older_history_after_budget_boundary(monkeypatch):
    mod = _load_context(monkeypatch, {"KLAI_CONTEXT_HISTORY_BUDGET_CHARS": "40"})
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "old-gap"},
            {"role": "assistant", "content": "middle " + ("m" * 100)},
            {"role": "user", "content": "recent-ok"},
            {"role": "user", "content": "latest"},
        ],
        requested_model="klai-primary",
    )

    rendered = "\n".join(
        message.get("content", "")
        for message in result.messages
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )
    assert "recent-ok" in rendered
    assert "middle" not in rendered
    assert "old-gap" not in rendered
    assert result.meta["omitted_history_messages"] == 2


def test_orchestrator_can_normalize_without_history_budget_before_scope(monkeypatch):
    mod = _load_context(monkeypatch, {"KLAI_CONTEXT_HISTORY_BUDGET_CHARS": "1"})
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "very old text"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": [{"type": "text", "text": "latest"}]},
        ],
        requested_model="klai-primary",
        apply_history_budget=False,
    )

    rendered = "\n".join(
        message.get("content", "")
        for message in result.messages
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    )
    assert "very old text" in rendered
    assert "old answer" in rendered
    assert result.messages[-1]["content"] == "latest"
    assert result.meta["history_budget_applied"] is False
    assert result.meta["omitted_history_messages"] == 0


def test_orchestrator_reports_unknown_shapes_without_silent_normalization(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()
    message = {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
    }

    result = orchestrator.assemble([message], requested_model="klai-primary")

    assert result.messages == [message]
    assert result.meta["unknown_content_shapes"] == 1
    assert result.meta["normalized_text_part_messages"] == 0
