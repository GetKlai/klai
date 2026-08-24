"""graphiti asks the model to see its schema, not to obey it.

GetKlai/klai#1148. `OpenAIGenericClient._build_response_format` sends
json_schema deliberately without `strict: true`, reasoning that
constrained-decoding servers enforce it anyway. Mistral behind LiteLLM does
not: without the flag it treats the schema as advice and invents shapes once a
response carries many items, while the first dozen are fine.

    extracted_entities.26.episode_indices
      Input should be a valid list [input_value=0, input_type=int]
    extracted_entities.15.entity_type_id
      Field required [input_value={'entity_type_id_id': 0, ...}]

Pydantic rejects the response and the whole episode is lost. Measured
2026-08-24 with graphiti's own ExtractedEntities schema through this LiteLLM:
without the flag the rebuild lost 2 of every 4 documents; with it, 39 entities
in one response and none malformed.
"""

from __future__ import annotations

from pydantic import BaseModel

from knowledge_ingest import graph as graph_module


class _Model(BaseModel):
    name: str


def _client():
    from graphiti_core.llm_client import openai_generic_client as ogc

    graph_module._install_strict_structured_output()
    return ogc.OpenAIGenericClient.__new__(ogc.OpenAIGenericClient)


def test_json_schema_requests_are_strict():
    client = _client()
    client.structured_output_mode = "json_schema"

    payload = client._build_response_format(_Model)

    assert payload["type"] == "json_schema"
    assert payload["json_schema"]["strict"] is True, (
        "the model is shown the schema but not held to it -- it drifts once a "
        "response carries many items, and the episode is lost"
    )
    assert payload["json_schema"]["schema"], "the schema itself must still be sent"


def test_json_object_mode_is_left_alone():
    """There is no schema to be strict about; adding the flag would be nonsense."""
    client = _client()
    client.structured_output_mode = "json_object"

    payload = client._build_response_format(_Model)

    assert payload == {"type": "json_object"}


def test_no_response_model_is_left_alone():
    client = _client()
    client.structured_output_mode = "json_schema"

    assert client._build_response_format(None) == {"type": "json_object"}


def test_install_is_idempotent():
    """Runs on every client construction; must not wrap itself."""
    from graphiti_core.llm_client import openai_generic_client as ogc

    graph_module._install_strict_structured_output()
    first = ogc.OpenAIGenericClient._build_response_format
    graph_module._install_strict_structured_output()
    assert ogc.OpenAIGenericClient._build_response_format is first
