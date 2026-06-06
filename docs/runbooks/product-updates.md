# Product updates

Product updates are the short "What's new" items shown behind the megaphone in the portal top bar.

## Security contract

Publish with the operator script only:

```text
klai-portal/backend/scripts/create_product_update.py
```

The script must run from trusted infra or an equivalent operator shell with production backend database access. There is no portal/API publish endpoint. Infra access is the admin boundary.

Do not publish product updates by:

- adding a portal/admin HTTP endpoint
- adding Alembic data migrations for content
- using browser cookies, bearer tokens, or E2E login state
- hand-writing SQL rows outside the backend service

The product update row stores:

- `created_at`: release/display date shown to users
- `published_at`: server-side publish time
- `created_by_user_id`: null for operator-script publishes
- `published_via`: publish path, currently `operator_script`
- `dedupe_key`: idempotency key, so retrying a publish does not create duplicates
- `commit_shas`: related commits for internal provenance

## Authoring rules

Write in English. Apply:

1. `.claude/rules/gtm/klai-brand-voice.md`
2. `.claude/rules/gtm/klai-humanizer.md`

Do not use `.claude/rules/gtm/mark-tone-of-voice.md`.

Use the customer filter:

- Only publish what a customer can see, use, trust, or recover from.
- Security improvements may be published when the customer promise is clear.
- Skip CI, dependency bumps, refactors, internal-only admin polish, and reverted work.
- Keep titles under 240 characters and bodies under 4000 characters.

## Publish one update

Run from `klai-portal/backend` in the trusted operator environment:

```bash
uv run python scripts/create_product_update.py \
  --title "Sources are clearer in chat" \
  --body "Knowledge answers now keep their sources visible in more chat paths. Klai also cleans old source footers before the next answer, so long conversations no longer collect duplicate source blocks." \
  --created-at 2026-06-06T12:00:00Z \
  --dedupe-key release:2026-06-06:sources \
  --commit 08e1e1f0 \
  --commit b9fad488
```

## Publish a batch

Create a local JSON file outside git or under `.context/`:

```json
[
  {
    "title": "Sources are clearer in chat",
    "body": "Knowledge answers now keep their sources visible in more chat paths. Klai also cleans old source footers before the next answer, so long conversations no longer collect duplicate source blocks.",
    "created_at": "2026-06-06T12:00:00Z",
    "dedupe_key": "release:2026-06-06:sources",
    "commit_shas": ["08e1e1f0", "b9fad488", "af202284"]
  }
]
```

Then publish from `klai-portal/backend`:

```bash
uv run python scripts/create_product_update.py \
  --json ../../.context/product-updates.json
```

Preview without publishing:

```bash
uv run python scripts/create_product_update.py --json ../../.context/product-updates.json --dry-run
```

## Verify

After publishing:

1. Open the portal as a normal authenticated user.
2. Open the megaphone.
3. Confirm the update appears with the intended date.
4. Open the update and confirm the body text is correct.
5. Mark it read and refresh. The unread indicator should clear.

If the megaphone shows "No product updates yet", the API returned zero rows. Do not add a migration. Check that the operator script ran in the intended environment and committed successfully.
