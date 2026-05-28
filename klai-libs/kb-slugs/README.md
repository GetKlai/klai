# klai-kb-slugs

Canonical Knowledge Base slug helpers shared between `klai-portal` (provisioning side)
and `klai-retrieval-api` (search side). One library = one slug template = no drift.

## Why this exists

The slug template ``personal-<zitadel_user_id>`` was originally defined inline in
``klai-portal/backend/app/services/default_knowledge_bases.py`` and reconstructed
ad-hoc in ``deploy/litellm/klai_knowledge.py`` and elsewhere. The 2026-05-27
"Jantine incident" surfaced that this kind of multi-file string contract drifts
silently (see ``url-shape-multi-file-drift`` pitfall in
``.claude/rules/klai/pitfalls/process-rules.md``).

This package centralises the template. Both portal-api and retrieval-api import
``personal_kb_slug`` so a future rename moves in one place.

## Usage

```python
from klai_kb_slugs import personal_kb_slug

slug = personal_kb_slug("300000000000000002")
# -> "personal-300000000000000002"
```

## SPEC

- SPEC-RAG-PERSONAL-SCOPE-001 (server-side enforcement of Persoonlijk-KB narrowing)
- SPEC-PORTAL-KB-OWNERSHIP-001 (consumer: route-level firewall magic-slug shortcut)
