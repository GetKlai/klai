"""Deterministic stand-ins for graphiti's LLM, embedder and reranker.

Nothing here touches the network. The fakes implement graphiti's own client
interfaces, so every Cypher/RediSearch query graphiti builds still runs for
real against FalkorDB.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate
from graphiti_core.prompts.dedupe_nodes import NodeResolutions
from graphiti_core.prompts.extract_edges import EdgeTimestamps, ExtractedEdges
from graphiti_core.prompts.extract_nodes import ExtractedEntities, SummarizedEntities
from pydantic import BaseModel


def vector_for(text: str, dim: int) -> list[float]:
    """Unit vector seeded by the text: equal text, equal vector."""
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    rng = random.Random(seed)  # noqa: S311 - deterministic test vector, not security
    raw = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


class FakeEmbedder(EmbedderClient):
    """Equal text, equal vector; ``similar`` maps a name to the name it should embed like.

    A real embedder puts "Acme Holdings" next to "Acme"; the hash vectors would not,
    and without a near candidate graphiti never escalates a node to the LLM.
    """

    def __init__(self, dim: int, similar: dict[str, str] | None = None) -> None:
        self.dim = dim
        self.similar = similar if similar is not None else {}

    def _vector(self, text: str) -> list[float]:
        # Case and whitespace barely move a real embedding: "\nTABBED\t" sits next to "tabbed",
        # which is what lets graphiti find a stored node under a differently written name.
        text = self.similar.get(text, text)
        return vector_for(" ".join(text.lower().split()), self.dim)

    async def create(self, input_data: Any) -> list[float]:
        text = input_data if isinstance(input_data, str) else "\n".join(map(str, input_data))
        return self._vector(text)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in input_data_list]


class FakeCrossEncoder(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 1.0 / (index + 1)) for index, passage in enumerate(passages)]


def _prompt_list(prompt: str, tag: str) -> list[dict[str, Any]]:
    """The JSON list graphiti renders between ``<TAG>`` and ``</TAG>`` in a prompt."""
    start = prompt.index(f"<{tag}>") + len(tag) + 2
    return json.loads(prompt[start : prompt.index(f"</{tag}>")])


class FakeLLM(LLMClient):
    """Extracts the names in ``entities`` and chains an edge between neighbours.

    ``duplicates`` maps an extracted name to the stored name it is the same entity as.
    The dedupe answer is read from the prompt graphiti really built, so a node only gets
    resolved if graphiti sent it, with that candidate, to ``_resolve_with_llm``.
    """

    def __init__(self) -> None:
        super().__init__(LLMConfig(api_key="fake", model="fake"), cache=False)
        self.entities: list[str] = []
        self.duplicates: dict[str, str] = {}
        self.dedupe_prompts: list[tuple[list[str], list[str]]] = []

    async def _generate_response(
        self,
        messages: Any,
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 0,
        model_size: ModelSize = ModelSize.medium,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        if response_model is ExtractedEntities:
            return {
                "extracted_entities": [
                    {"name": name, "entity_type_id": 0, "episode_indices": [0]}
                    for name in self.entities
                ]
            }
        if response_model is ExtractedEdges:
            return {
                "edges": [
                    {
                        "source_entity_name": source,
                        "target_entity_name": target,
                        "relation_type": "RELATES_TO_FIXTURE",
                        "fact": f"{source} is linked to {target} in the fixture corpus",
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                    for source, target in zip(self.entities, self.entities[1:], strict=False)
                ]
            }
        if response_model is NodeResolutions:
            prompt = messages[-1].content
            sent = _prompt_list(prompt, "ENTITIES")
            candidates = {
                c["name"]: c["candidate_id"] for c in _prompt_list(prompt, "EXISTING ENTITIES")
            }
            self.dedupe_prompts.append(([e["name"] for e in sent], list(candidates)))
            return {
                "entity_resolutions": [
                    {
                        "id": entity["id"],
                        "name": entity["name"],
                        "duplicate_candidate_id": candidates.get(
                            self.duplicates.get(entity["name"], ""), -1
                        ),
                    }
                    for entity in sent
                ]
            }
        if response_model is SummarizedEntities:
            return {"summaries": []}
        if response_model is EdgeDuplicate:
            return {"duplicate_facts": [], "contradicted_facts": []}
        if response_model is EdgeTimestamps:
            return {"valid_at": None, "invalid_at": None}
        raise AssertionError(f"contract fake has no answer for {response_model}")
