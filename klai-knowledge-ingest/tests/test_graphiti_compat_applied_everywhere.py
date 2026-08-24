"""Every entry point that builds a Graphiti client must apply the FalkorDB patch.

GetKlai/klai#1214. klai_graphiti_compat rewrites graphiti's edge fulltext
search, which as shipped re-finds each hit with

    MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity)

FalkorDB does not answer that inline property pattern from the uuid index, so
it scans every RELATES_TO edge once per hit. Measured on the Voys graph
(18,031 edges): unpatched the query ran past 140 s and died on FalkorDB's 1 s
timeout; patched it takes 2.99 ms.

app.py applies the patch at import, so the service was always covered. The
operator entry point was not: `python -m knowledge_ingest.backfill` never
imports app, so every episode the graph rebuild wrote failed, across twenty
documents and every configuration tried.
"""

from __future__ import annotations

import inspect

from knowledge_ingest import graph as graph_module


def test_get_graphiti_applies_the_compat_patch():
    """_get_graphiti is the single place every path builds a client."""
    source = inspect.getsource(graph_module._get_graphiti)
    assert "_patch_graphiti.apply()" in source, (
        "a client can be built without the FalkorDB patch -- any entry point "
        "that does not import app.py will scan the graph per fulltext hit"
    )


def test_the_patch_is_idempotent():
    """_get_graphiti runs per client construction; app.py has already applied it."""
    from knowledge_ingest import _patch_graphiti

    _patch_graphiti.apply()
    _patch_graphiti.apply()  # must not raise or stack


def test_the_shared_patch_still_rewrites_the_edge_search():
    """If graphiti changes the query, the shared rewrite must be revisited.

    Read the file rather than the object: other tests mock parts of
    graphiti_core, and a mocked object would make this pass or fail for
    reasons unrelated to graphiti's source.
    """
    import pathlib

    import klai_graphiti_compat

    source = pathlib.Path(klai_graphiti_compat.__file__).read_text(encoding="utf-8")
    assert "startNode(e) AS n" in source, "the shared rewrite no longer emits startNode"
    assert "endNode(e) AS m" in source
