"""Dimension constants shared by TEI, graphiti compat, and ANN index DDL."""

from __future__ import annotations

import klai_graphiti_compat

from knowledge_ingest import embedder


def test_embedder_dimension_matches_graphiti_compat() -> None:
    assert embedder.EMBED_DIM == klai_graphiti_compat.GRAPHITI_VECTOR_DIMENSION == 1024
