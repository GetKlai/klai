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
    assert meta["final_model"] == "klai-large"
    assert meta["profile_selection_phase"] == "requested_model"
    assert meta["model_profile"] == "klai-large"
    assert meta["upstream_model"] == "mistral-large-2512"
    assert meta["token_counter_model"] == "mistral/mistral-large-2512"
    assert meta["history_budget_chars"] == 2000
    assert meta["history_budget_tokens"] == 12000
    assert meta["output_reserve_chars"] == 48000
    assert meta["output_reserve_tokens"] == 12000


def test_orchestrator_uses_final_model_profile_after_router(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [{"role": "user", "content": "Hallo"}],
        requested_model="klai-primary",
        final_model="klai-large",
    )

    assert result.meta["requested_model"] == "klai-primary"
    assert result.meta["final_model"] == "klai-large"
    assert result.meta["profile_selection_phase"] == "post_router_final_model"
    assert result.meta["model_profile"] == "klai-large"
    assert result.meta["history_budget_chars"] == 2000


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


def test_orchestrator_omits_internal_tool_history_for_mistral(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "system", "content": "Klai instructions."},
            {"role": "user", "content": "Search the KB."},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Visible answer."},
                    {
                        "type": "tool_call",
                        "tool_call": {"name": "search_knowledge", "args": "{}"},
                    },
                ],
                "tool_calls": [{"id": "call_1", "function": {"name": "search"}}],
            },
            {
                "role": "tool",
                "name": "search_knowledge",
                "tool_call_id": "call_1",
                "content": '{"result": "internal"}',
            },
            {"role": "user", "content": "Continue."},
        ],
        requested_model="klai-large",
    )

    roles = [message.get("role") for message in result.messages if isinstance(message, dict)]
    assert roles == ["system", "user", "assistant", "user"]
    assert result.messages[2]["content"] == "Visible answer."
    assert "tool_calls" not in result.messages[2]
    assert all(
        not isinstance(message.get("content"), list)
        for message in result.messages
        if isinstance(message, dict)
    )
    assert result.meta["omitted_tool_messages"] == 1
    assert result.meta["omitted_tool_content_parts"] == 1
    assert "internal_tool_messages_omitted" in result.meta["reason_codes"]
    assert "internal_tool_content_parts_omitted" in result.meta["reason_codes"]


def test_orchestrator_preserves_active_tool_result_for_mistral(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "Zoek in de knowledge base naar Zurich."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "tool_call": {"name": "search_knowledge", "args": "{}"},
                    }
                ],
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "search_knowledge"}}
                ],
            },
            {
                "role": "tool",
                "name": "search_knowledge",
                "tool_call_id": "call_1",
                "content": "Project Zurich gebruikt testcode ZURICH-CTX-2606.",
            },
        ],
        requested_model="klai-large",
    )

    provider_messages = [
        message for message in result.messages if isinstance(message, dict)
    ]
    assert [message["role"] for message in provider_messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert provider_messages[1]["tool_calls"][0]["id"] == "call_1"
    assert provider_messages[-1]["tool_call_id"] == "call_1"
    assert "ZURICH-CTX-2606" in provider_messages[-1]["content"]
    assert result.meta["omitted_tool_messages"] == 0
    assert result.meta["active_tool_calls_preserved"] == 1
    assert result.meta["active_tool_results_preserved"] == 1
    assert "active_tool_results_preserved" in result.meta["reason_codes"]


def test_orchestrator_preserves_multiple_active_tool_results(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "Search twice."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "search_knowledge"}},
                    {"id": "call_2", "function": {"name": "search_knowledge"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "First result."},
            {"role": "tool", "tool_call_id": "call_2", "content": "Second result."},
        ],
        requested_model="klai-large",
    )

    provider_messages = [
        message for message in result.messages if isinstance(message, dict)
    ]
    assert [message["role"] for message in provider_messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert provider_messages[-1]["content"] == "Second result."
    assert result.meta["active_tool_results_preserved"] == 2


def test_orchestrator_keeps_empty_active_tool_result_provider_safe(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "Search empty."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "function": {"name": "search"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "   "},
        ],
        requested_model="klai-large",
    )

    provider_messages = [
        message for message in result.messages if isinstance(message, dict)
    ]
    assert provider_messages[-1]["role"] == "tool"
    assert provider_messages[-1]["content"] == mod.ACTIVE_TOOL_EMPTY_RESULT_PLACEHOLDER
    assert result.meta["empty_active_tool_results"] == 1
    assert result.meta["trailing_assistant_repaired"] == 0


def test_orchestrator_serializes_structured_active_tool_result(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "Search structured."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "search"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [{"text": "A"}, {"text": "B"}],
            },
        ],
        requested_model="klai-large",
    )

    provider_messages = [
        message for message in result.messages if isinstance(message, dict)
    ]
    assert provider_messages[-1]["role"] == "tool"
    assert provider_messages[-1]["content"] == '[{"text": "A"}, {"text": "B"}]'


def test_orchestrator_budget_merges_placeholder_and_drops_orphan_assistant(
    monkeypatch,
):
    mod = _load_context(monkeypatch, {"KLAI_CONTEXT_HISTORY_BUDGET_CHARS": "40"})
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "system", "content": "Grounded KB instructions."},
            {"role": "user", "content": "old user " + ("x" * 80)},
            {"role": "assistant", "content": "recent orphan assistant"},
            {"role": "user", "content": "latest"},
        ],
        requested_model="klai-primary",
    )

    roles = [message["role"] for message in result.messages if isinstance(message, dict)]
    assert roles == ["system", "user"]
    assert result.messages[0]["content"].startswith("Grounded KB instructions.")
    assert mod.HISTORY_BUDGET_CONTEXT_PLACEHOLDER in result.messages[0]["content"]
    rendered = "\n".join(
        message.get("content", "")
        for message in result.messages
        if isinstance(message, dict)
    )
    assert "old user" not in rendered
    assert "recent orphan assistant" not in rendered
    assert result.meta["omitted_history_messages"] == 2


def test_orchestrator_strips_top_level_tool_calls_for_mistral(monkeypatch):
    mod = _load_context(monkeypatch)
    orchestrator = mod.KlaiContextOrchestrator()

    result = orchestrator.assemble(
        [
            {"role": "user", "content": "Search first."},
            {
                "role": "assistant",
                "content": "I need a tool.",
                "tool_calls": [{"id": "call_1", "function": {"name": "search"}}],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_2", "function": {"name": "search"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "{}",
            },
            {
                "role": "tool",
                "tool_call_id": "call_2",
                "content": "{}",
            },
            {"role": "assistant", "content": "The answer is in the source."},
            {"role": "user", "content": "Continue."},
        ],
        requested_model="klai-large",
    )

    provider_messages = [
        message for message in result.messages if isinstance(message, dict)
    ]
    assert [message["role"] for message in provider_messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert provider_messages[1]["content"] == "The answer is in the source."
    assert all("tool_calls" not in message for message in provider_messages)
    assert result.meta["omitted_tool_messages"] == 2
    assert result.meta["omitted_tool_content_parts"] == 2
    assert result.meta["repaired_role_sequence_messages"] == 2
