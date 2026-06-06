"""Small adapters for dict/object LiteLLM response shapes."""

from __future__ import annotations

import copy


def get_choice_message(choice: object, key: str) -> object:
    if isinstance(choice, dict):
        return choice.get(key)
    return getattr(choice, key, None)


def get_message_content(message: object) -> object:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def get_choice_finish_reason(choice: object) -> object:
    if isinstance(choice, dict):
        return choice.get("finish_reason")
    return getattr(choice, "finish_reason", None)


def set_choice_finish_reason(choice: object, value: object) -> None:
    if isinstance(choice, dict):
        choice["finish_reason"] = value
    else:
        setattr(choice, "finish_reason", value)


def set_message_content(message: object, content: object) -> None:
    if isinstance(message, dict):
        message["content"] = content
    else:
        setattr(message, "content", content)


def set_message_field(message: object, key: str, value: object) -> None:
    if isinstance(message, dict):
        message[key] = value
    else:
        setattr(message, key, value)


def delete_message_field(message: object, key: str) -> None:
    if isinstance(message, dict):
        message.pop(key, None)
    elif hasattr(message, key):
        delattr(message, key)


def get_response_choices(response: object) -> object:
    if isinstance(response, dict):
        return response.get("choices") or []
    return getattr(response, "choices", []) or []


def stream_item_has_finish_reason(item: object) -> bool:
    return any(
        bool(get_choice_finish_reason(choice)) for choice in get_response_choices(item)
    )


def split_stream_footer_from_stop_item(item: object) -> object:
    """Return a non-final copy carrying content/sources, leaving item as pure stop."""
    footer_item = copy.deepcopy(item)
    for choice in get_response_choices(footer_item):
        set_choice_finish_reason(choice, None)
    for choice in get_response_choices(item):
        delta = get_choice_message(choice, "delta")
        if delta is not None:
            set_message_content(delta, "")
            delete_message_field(delta, "sources")
    return footer_item
