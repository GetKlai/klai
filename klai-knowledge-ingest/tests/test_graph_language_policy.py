"""Language policy: source-language facts, canonical entity names.

GetKlai/klai#1148. The corpus is Dutch, English and German today, with
French, Spanish and Portuguese arriving with the multi-country rollout, so
neither half of this can be left to a default.

graphiti ships its own instruction ending in "Otherwise, output English" and
appends it to every system message AFTER any custom extraction instruction.
That default is what put English facts on Dutch pages, and it is why the
policy is installed over the function rather than written into a prompt.
"""

from __future__ import annotations

import types

import graphiti_core.llm_client as llm_pkg
import pytest

from knowledge_ingest import graph as graph_module


def _bound_modules() -> list[types.ModuleType]:
    """Every graphiti llm_client module holding a reference to the hook."""
    return [
        module
        for name in dir(llm_pkg)
        if isinstance(module := getattr(llm_pkg, name, None), types.ModuleType)
        and hasattr(module, "get_extraction_language_instruction")
    ]


@pytest.fixture(autouse=True)
def _install():
    graph_module._install_language_policy()


def test_every_binding_is_patched_not_just_the_definition():
    """Each client does `from .client import ...`, binding the name at import.

    Patching only ``llm_client.client`` would leave every concrete client
    calling the original. This is the assertion that catches a graphiti bump
    adding a client module the installer does not reach.
    """
    modules = _bound_modules()
    assert modules, "no graphiti llm_client module exposes the hook -- API moved"

    for module in modules:
        rendered = module.get_extraction_language_instruction()
        assert "LANGUAGE POLICY" in rendered, (
            f"{module.__name__} still calls graphiti's own instruction"
        )
        assert "Otherwise, output English" not in rendered, (
            f"{module.__name__} still defaults non-English sources to English"
        )


def test_the_client_we_actually_use_is_covered():
    """OpenAIGenericClient is the one pointed at LiteLLM (graph.py)."""
    from graphiti_core.llm_client import openai_generic_client

    assert "LANGUAGE POLICY" in openai_generic_client.get_extraction_language_instruction()


def test_facts_follow_the_source_language():
    policy = graph_module._LANGUAGE_POLICY
    assert "same language as the text it" in policy
    assert "Never translate fact text" in policy
    assert "never default to English" in policy


def test_entity_names_are_canonical_so_the_graph_joins_across_languages():
    """The join key cannot vary by source language.

    graphiti resolves entities on character 3-gram shingles of the name
    (dedup_helpers._shingles). "Toestel" and "Device" share none, so without a
    pivot one concept becomes one node per language.
    """
    policy = graph_module._LANGUAGE_POLICY
    assert "canonical English" in policy
    assert "Toestel" in policy and "Device" in policy


def test_proper_nouns_survive_both_rules():
    """Translating "Belplan" would break the join with every page naming it."""
    policy = graph_module._LANGUAGE_POLICY
    assert "never translated" in policy
    assert "Belplan" in policy
    assert "verbatim" in policy


def test_language_is_stated_once():
    """Rule 2 used to live in the extraction instructions as well.

    Two copies of a language rule drift; the extraction instructions now say
    where it moved instead of repeating it.
    """
    instructions = graph_module._EXTRACTION_INSTRUCTIONS
    assert "language of the source text" not in instructions
    assert "_LANGUAGE_POLICY" in instructions
