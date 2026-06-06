# Product updates

Product updates are the short "What's new" items shown behind the megaphone in the portal top bar.

## Security contract

Publish through the platform-admin API only:

```text
POST /api/admin/platform/product-updates
```

That endpoint is guarded by `require_platform_admin()`. A normal admin, tenant user, script with database access, or migration must not bypass it.

Do not publish product updates by:

- writing rows directly into `product_updates`
- adding Alembic data migrations for content
- using an internal service secret
- adding a second publish endpoint with weaker auth

The product update row stores:

- `created_at`: release/display date shown to users
- `published_at`: server-side publish time
- `created_by_user_id`: platform admin who published it
- `published_via`: publish path, currently `admin_api`
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

Run from `klai-portal/backend`:

```bash
uv run python scripts/create_product_update.py \
  --api-url https://my.getklai.com \
  --cookie "$KLAI_ADMIN_COOKIE" \
  --title "Sources are clearer in chat" \
  --body "Knowledge answers now keep their sources visible in more chat paths. Klai also cleans old source footers before the next answer, so long conversations no longer collect duplicate source blocks." \
  --created-at 2026-06-06T12:00:00Z \
  --dedupe-key release:2026-06-06:sources \
  --commit 08e1e1f0 \
  --commit b9fad488
```

`KLAI_ADMIN_COOKIE` must be a raw cookie header from a logged-in platform-admin portal session. Treat it like a session secret. Do not commit it, paste it into issue comments, or store it in repo files.

A platform-admin bearer can be used instead with `--token "$KLAI_ADMIN_TOKEN"` when that is the active operator auth method.

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

Then publish:

```bash
uv run python scripts/create_product_update.py \
  --api-url https://my.getklai.com \
  --cookie "$KLAI_ADMIN_COOKIE" \
  --json .context/product-updates.json
```

Preview without publishing:

```bash
uv run python scripts/create_product_update.py --json .context/product-updates.json --dry-run
```

## Verify

After publishing:

1. Open the portal as a normal authenticated user.
2. Open the megaphone.
3. Confirm the update appears with the intended date.
4. Open the update and confirm the body text is correct.
5. Mark it read and refresh. The unread indicator should clear.

If the megaphone shows "No product updates yet", the API returned zero rows. Do not add a migration. Check that the publish command succeeded against the intended `--api-url` and that the authenticated user was a platform admin.
