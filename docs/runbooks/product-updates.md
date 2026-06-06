# Product updates

Product updates are the short "What's new" items shown behind the megaphone in the portal top bar.

The operational publishing workflow is private and lives in:

```text
klai-infra/PRODUCT_UPDATES.md
```

Keep this public monorepo file limited to product/implementation context. Do not document operator publish credentials, production commands, or infra-only workflow details here.

Implementation notes:

- Publishing is done with `klai-portal/backend/scripts/create_product_update.py`.
- There is no portal/admin HTTP publish endpoint.
- Product update content must not be shipped as Alembic data migrations.
- `commit_shas`, `dedupe_key`, `published_at`, and `published_via` are stored for provenance and idempotency.
