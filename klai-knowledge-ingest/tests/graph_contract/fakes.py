"""Deterministic stand-ins for graphiti's LLM, embedder and reranker.

Nothing here touches the network. The fakes implement graphiti's own client
interfaces, so every Cypher/RediSearch query graphiti builds still runs for
real against FalkorDB.
"""

from __future__ import annotations

import hashlib
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
    def __init__(self, dim: int) -> None:
        self.dim = dim

    async def create(self, input_data: Any) -> list[float]:
        text = input_data if isinstance(input_data, str) else "\n".join(map(str, input_data))
        return vector_for(text, self.dim)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [vector_for(text, self.dim) for text in input_data_list]


class FakeCrossEncoder(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 1.0 / (index + 1)) for index, passage in enumerate(passages)]


class FakeLLM(LLMClient):
    """Extracts the names in ``entities`` and chains an edge between neighbours."""

    def __init__(self) -> None:
        super().__init__(LLMConfig(api_key="fake", model="fake"), cache=False)
        self.entities: list[str] = []

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
            return {"entity_resolutions": []}
        if response_model is SummarizedEntities:
            return {"summaries": []}
        if response_model is EdgeDuplicate:
            return {"duplicate_facts": [], "contradicted_facts": []}
        if response_model is EdgeTimestamps:
            return {"valid_at": None, "invalid_at": None}
        raise AssertionError(f"contract fake has no answer for {response_model}")
