"""Operator scripts for klai-knowledge-ingest.

Marker file so the package can be invoked as a module:

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.backfill_entity_names --org-id <org_id>

Without __init__.py, only `python scripts/<file>.py` worked, which forces
the operator to remember the file path. The -m form follows the project's
existing convention (e.g. python -m knowledge_ingest.eval ...).
"""
