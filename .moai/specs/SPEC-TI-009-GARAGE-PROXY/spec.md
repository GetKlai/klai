# SPEC-TI-009 — Garage KB-image auth-proxy

**Audit ref:** finding **B-4**
**Standards ref:** `standards.md` section 13
**Priority:** MED
**Status:** Ready

## Goal

Vervang anonieme Caddy → Garage `/kb-images/*` proxy door auth-proxy via portal-api. Eliminate "gelekte URL = permanent cross-tenant lees" risico.

**Architectuur-keuze (gemaakt):** Optie A — auth-proxy via portal-api endpoint. Optie B (presigned URLs met TTL) bewaard als fallback indien latentie/throughput issue.

## Acceptance criteria (EARS)

- **AC-1** Nieuwe portal-api route `GET /kb-images/{org_id}/{kb_slug}/{filename}`:
  - Verifieert dat `session.user.org_id == int(org_id)` OF widget-public-allowlist OF partner-api-key matches
  - Streamt content vanuit Garage S3 API (private, authenticated; niet website-mode)
  - Cache headers: `Cache-Control: private, max-age=86400`
- **AC-2** Caddy `Caddyfile`: `handle_path /kb-images/*` reverse-proxy naar `portal-api:8000` ipv `garage:3902`.
- **AC-3** Garage bucket `klai-images`: website-mode UIT (S3 API blijft authenticated).
- **AC-4** Bestaande URLs blijven werken (URL-format ongewijzigd; alleen achterkant wisselt).
- **AC-5** Geen cross-tenant access: tenant-A user die URL voor tenant-B image kopieert krijgt 403.

## Implementation

1. **Portal-api**: nieuwe route in `app/api/kb_images.py` (of bestaande images-module). S3 client via `klai_image_storage` lib.
2. **Caddy**: update `Caddyfile` block voor `/kb-images/*`. Behoud cache-control headers.
3. **Garage**: switch website-mode off via Garage CLI/API of via deploy-time config.
4. **Streaming**: gebruik `StreamingResponse` met content-type uit Garage object metadata.

## Tests

- `test_kb_images_auth_proxy.py`:
  - `test_authenticated_user_can_read_own_org_image()` → 200
  - `test_authenticated_user_cannot_read_foreign_org_image()` → 403
  - `test_unauthenticated_request_rejected()` → 401
  - `test_widget_public_image_works()` → 200 voor widget-allowlist
  - `test_404_for_nonexistent_object()` → 404

## Operator-step

```bash
# 1. Disable Garage website-mode
ssh core-01 "docker exec klai-core-garage-1 garage bucket website --deny klai-images"

# 2. Deploy Caddy + portal-api atomically (CI handles)
gh run watch
```

## Worktree

`klai-garage-proxy` — `feature/SPEC-TI-009-GARAGE-PROXY`.
