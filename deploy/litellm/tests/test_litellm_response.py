"""Contract tests for LiteLLM response-shape adapters."""

from __future__ import annotations

from types import SimpleNamespace

from klai_litellm_response import (
    get_choice_finish_reason,
    get_choice_message,
    get_message_content,
    get_response_choices,
    set_message_content,
    set_message_field,
    split_stream_footer_from_stop_item,
    stream_item_has_finish_reason,
)


def test_dict_response_accessors_read_and_write_message_fields():
    response = {
        "choices": [
            {
                "finish_reason": None,
                "message": {"content": "hello"},
            }
        ]
    }

    choice = get_response_choices(response)[0]
    message = get_choice_message(choice, "message")
    assert get_message_content(message) == "hello"
    assert get_choice_finish_reason(choice) is None

    set_message_content(message, "updated")
    set_message_field(message, "sources", [{"title": "Doc"}])

    assert response["choices"][0]["message"]["content"] == "updated"
    assert response["choices"][0]["message"]["sources"] == [{"title": "Doc"}]


def test_object_response_accessors_read_and_write_message_fields():
    message = SimpleNamespace(content="hello")
    choice = SimpleNamespace(finish_reason="stop", message=message)
    response = SimpleNamespace(choices=[choice])

    assert get_response_choices(response) == [choice]
    assert get_choice_message(choice, "message") is message
    assert get_message_content(message) == "hello"
    assert get_choice_finish_reason(choice) == "stop"

    set_message_content(message, "updated")
    set_message_field(message, "sources", [{"title": "Doc"}])

    assert message.content == "updated"
    assert message.sources == [{"title": "Doc"}]
    assert stream_item_has_finish_reason(response) is True


def test_split_stream_footer_from_stop_item_copies_footer_and_clears_original_stop_delta():
    item = {
        "choices": [
            {
                "finish_reason": "stop",
                "delta": {
                    "content": "final footer",
                    "sources": [{"title": "Doc"}],
                },
            }
        ]
    }

    footer_item = split_stream_footer_from_stop_item(item)

    assert footer_item is not item
    assert footer_item["choices"][0]["finish_reason"] is None
    assert footer_item["choices"][0]["delta"]["content"] == "final footer"
    assert footer_item["choices"][0]["delta"]["sources"] == [{"title": "Doc"}]
    assert item["choices"][0]["finish_reason"] == "stop"
    assert item["choices"][0]["delta"]["content"] == ""
    assert "sources" not in item["choices"][0]["delta"]
