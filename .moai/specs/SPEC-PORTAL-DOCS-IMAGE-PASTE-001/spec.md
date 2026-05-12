---
id: SPEC-PORTAL-DOCS-IMAGE-PASTE-001
title: Image paste/upload in de portal docs editor
version: 0.1.0
status: draft
created_at: 2026-05-12
owner: mark.vletter
domain: portal-docs
related_specs:
  - SPEC-KB-IMAGE-001            # initial Garage S3 image pipeline (connectors)
  - SPEC-KB-IMAGE-002            # content-addressed key format + ImageStore lib
  - SPEC-TI-009                  # /kb-images auth-proxy read route (the read symmetry)
  - SPEC-PORTAL-RBAC-REFACTOR-001 # 5-layer permission model (ProfileRole, _get_kb_or_404)
  - SPEC-SEC-CORS-001            # CORS + middleware ordering (read-route is already covered)
---

# SPEC-PORTAL-DOCS-IMAGE-PASTE-001 — Image paste/upload in de portal docs editor

## HISTORY

| Date       | Version | Author        | Note                                                                 |
|------------|---------|---------------|----------------------------------------------------------------------|
| 2026-05-12 | 0.1.0   | mark.vletter  | Initial draft. Eén-PR scope, write-symmetrie met SPEC-TI-009 GET     |

---

## 1. Goal & Outcome

### Goal

Maak het mogelijk om in de portal docs editor (`/app/docs/{kbSlug}/{pageId}`)
afbeeldingen uit het clipboard te plakken — zodat screenshots, knipsels uit
documentatie of drag-dropped images direct in een KB-pagina komen, opgeslagen
in Garage S3 en uitgeserveerd via de bestaande auth-proxied read-route.

### Outcome (success measured by)

- Cmd-V / Ctrl-V met een PNG of JPEG in het clipboard plaatst een
  `<image>`-block in de pagina, met een werkende `src` die laadt zonder
  console errors.
- Drag-and-drop van een lokaal bestand op de editor doet hetzelfde.
- Identieke bytes (zelfde screenshot, twee pagina's) leveren één S3-object
  op dankzij content-addressed key (`sha256` dedup uit de bestaande lib).
- Een PNG > 5 MB returneert HTTP 413 met een leesbare melding in de editor;
  géén partial-write in Garage.
- Een `.exe` met `image/png` Content-Type returneert HTTP 415 op de
  magic-byte check; géén Garage-object.
- Een upload met sessie van org A naar een KB van org B returneert HTTP 404
  via `_get_kb_or_404` — tenant-isolation gevalideerd.

### Non-goal

- Image insert via URL (slash-menu pad) — dat werkt vandaag al via BlockNote
  defaults; geen wijziging.
- Per-page orphan-image GC. Hetzelfde gat bestaat al voor connectors
  (zie `knowledge.md` § "Connector-delete cleanup must cover every store").
  Op KB-delete wordt de hele `{org_id}/images/{kb_slug}/` prefix nog steeds
  gewist — dat blijft het cleanup-pad.
- Image-edit features (crop, rotate, alt-text picker). BlockNote's eigen
  image-toolbar is voldoende voor MVP.
- SVG-upload via paste/drag-drop (zie REQ-5 — bewust geweerd).
- Page-level `edit_access` enforcement op de write-route. KB-level scope
  is de MVP-grens; zie Open questions.

---

## 2. Background

De docs editor [`BlockPageEditor`](../../../klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx)
gebruikt BlockNote met een `useCreateBlockNote`-config die `schema` en een
custom markdown-aware `pasteHandler` zet, maar **géén `uploadFile`-callback**.
BlockNote's contract is `type uploadFile = (file: File) => Promise<string>` —
zonder die callback negeert het editor binaire clipboard-items.

Portal-api heeft via SPEC-TI-009 al een auth-proxied **read**-route:

```
GET /kb-images/{org_id}/{kb_slug}/{filename}
  → src: klai-portal/backend/app/api/kb_images.py:157
  → object key: {org_id}/images/{kb_slug}/{sha256}.{ext}
  → bucket: settings.garage_kb_bucket via minio SDK
  → auth: session.org_id OR partner-key org_id MUST equal path org_id
```

`klai-libs/image-storage` bevat al een productie-rijpe `ImageStore`-class met
content-addressed dedup, 5 MB size-guard, en magic-byte MIME validatie
(`_ALLOWED_IMAGE_MIMES = {jpeg, png, gif, webp}` + SVG-signature check). Die
class wordt vandaag door `klai-connector` en `klai-knowledge-ingest` gebruikt.

Wat ontbreekt is de **write-symmetrie**: een POST-route op portal-api die
bytes accepteert en aan dezelfde key-conventie schrijft. Zodra die er is,
plus een `uploadFile`-callback in `BlockPageEditor`, werkt clipboard-paste,
drag-drop én de slash-menu "Image"-flow zonder verdere wijzigingen — want
BlockNote's default paste-volgorde detecteert `Files` al vóór HTML/Markdown
en delegeert naar `uploadFile`.

Zie de research-bevinding van 2026-05-12 in het ontwerpgesprek dat aan deze
SPEC voorafging.

---

## 3. Scope

| In | Out |
|---|---|
| `POST /kb-images/{kb_slug}` op portal-api met `multipart/form-data` `file` veld | Cross-org upload, partner-key auth (niet nodig — alleen BFF-sessie) |
| `uploadFile`-callback in `BlockPageEditor.useCreateBlockNote` | Wijziging aan de bestaande `pasteHandler` |
| Hergebruik van `klai_image_storage.ImageStore` + `MAX_IMAGE_SIZE = 5 MB` | Nieuwe storage-laag of nieuwe lib |
| Magic-byte MIME validatie via `ImageStore.validate_image` | Per-page refcount tabel `docs.page_images` (volgt evt. in latere SPEC) |
| SVG-reject voor user uploads (`mime == "image/svg+xml"` → 415) | Connector-pad voor SVG (blijft accepteren) |
| Tests: auth, size, MIME, dedup, cross-tenant block, SVG-reject | E2E browser-test (handmatige smoketest is voldoende) |
| @MX:ANCHOR op de nieuwe route met REASON-regel over tenant-isolation | Wijziging aan de read-route response headers (CSP, Content-Disposition) |

---

## 4. EARS Requirements

### REQ-1 — Multipart image upload route op portal-api

**When** een geauthenticeerde caller met BFF-sessie een `POST` doet naar
`/api/kb-images/{kb_slug}` met `multipart/form-data` (veld `file`),
**the system** MUST de bytes opslaan via `ImageStore.upload_image(
str(perms.org_id), kb_slug, data, ext)` en een JSON-response teruggeven van
de vorm `{"url": "/kb-images/{org_id}/images/{kb_slug}/{sha256}.{ext}",
"deduplicated": bool}`.

De route MUST onder de bestaande `kb_images.py`-router landen (zelfde file,
zelfde router include). De org_id MUST uit `perms.org_id` komen via
`Depends(get_caller_at_least(ProfileRole.PERSONAL))` — **niet** uit een
pad-parameter.

### REQ-2 — KB-scoped authorization

**When** REQ-1 wordt uitgevoerd, **the system** MUST de KB resolven via
`_get_kb_or_404(kb_slug, perms.org_id, db)`. Een sessie van een andere org
MUST een HTTP 404 krijgen (niet 403 — conform `portal-security.md` rule
"return 404 for private resources … never leak existence").

### REQ-3 — Size guard

**When** REQ-1 een body ontvangt waarbij `len(data) > 5 * 1024 * 1024`
(de `MAX_IMAGE_SIZE` constante uit `klai_image_storage.storage`),
**the system** MUST HTTP 413 returneren MET een leesbare detail-string
(`"Image too large (max 5 MB)"`), en MUST geen S3-write doen.

### REQ-4 — Magic-byte MIME validatie

**When** REQ-1 een body ontvangt, **the system** MUST
`ImageStore.validate_image(data)` aanroepen. Returnt die `None` → HTTP 415
`"Unsupported image type"`. Returnt die een MIME die niet in
`{image/jpeg, image/png, image/gif, image/webp}` valt → HTTP 415
(SVG-reject — zie REQ-5).

### REQ-5 — SVG geweerd voor user uploads

**When** REQ-4 een MIME `image/svg+xml` detecteert, **the system** MUST
HTTP 415 returneren met detail `"SVG uploads not supported"`. Reden: de
bestaande read-route zet geen Content-Security-Policy header en stream't
SVG inline; een SVG-met-`<script>` zou via directe URL-navigatie als XSS
laden. Connector-pad blijft SVG accepteren omdat connectors een andere
trust-boundary hebben (bron-content uit externe systemen, niet user-paste).

### REQ-6 — `uploadFile`-callback in BlockPageEditor

**When** `BlockPageEditor` rendert in een KB-page edit context,
**the system** MUST `useCreateBlockNote({ uploadFile: async (file: File) =>
{ ... } })` configureren zodat BlockNote's default Files-paste-pad een
upload naar `POST /api/kb-images/{kbSlug}` doet en de teruggegeven `url`
in het image-block plaatst. De bestaande custom `pasteHandler` MUST
ongewijzigd blijven (die markdown-detect logica is orthogonaal).

### REQ-7 — Tenant-isolatie observability

**When** REQ-1 of REQ-2 een cross-tenant attempt detecteert (path-KB
hoort bij een andere org dan `perms.org_id`), **the system** MUST een
structlog `warning` emit met sleutel `kb_image_upload_cross_tenant_blocked`
en velden `caller_org_id`, `kb_slug` — symmetrisch met de bestaande
`kb_image_cross_tenant_blocked` log van de read-route.

### REQ-8 — @MX:ANCHOR op de nieuwe route

De nieuwe `upload_kb_image`-handler MUST een `@MX:ANCHOR` carry'en met
`@MX:REASON` die expliciet zegt: "Single enforcement point for KB-scoped
image writes. Changing the auth dependency, the kb_slug → org_id binding,
or the SVG-reject branch breaks tenant isolation or opens an XSS path."
En een `@MX:SPEC: SPEC-PORTAL-DOCS-IMAGE-PASTE-001` regel.

---

## 5. Acceptance Criteria

Elk criterium MUST een passing pytest of een handmatig geverifieerde
browser-flow opleveren.

| # | Criterium | Verificatie |
|---|---|---|
| AC-1 | Een PNG van 200 KB uploaden met geldige BFF-sessie → 200 OK + `{url, deduplicated:false}` | pytest tegen FastAPI TestClient |
| AC-2 | Dezelfde PNG nogmaals uploaden → 200 OK + `deduplicated:true`, geen tweede S3 put | pytest + minio mock dat `put_object` call-count telt |
| AC-3 | Een PNG van 6 MB uploaden → 413, body bevat `"max 5 MB"` | pytest |
| AC-4 | Een `.exe` met `Content-Type: image/png` → 415 `"Unsupported image type"` | pytest met crafted bytes |
| AC-5 | Een geldige SVG (`<svg>…</svg>`) → 415 `"SVG uploads not supported"` | pytest |
| AC-6 | Sessie van org A naar `kb_slug` van org B → 404, geen `put_object` call | pytest met twee org-fixtures |
| AC-7 | Onauth'd request zonder sessie → 401 | pytest |
| AC-8 | Cross-tenant attempt emit `kb_image_upload_cross_tenant_blocked` warning | pytest capture met `caplog` of `structlog` testing helpers |
| AC-9 | Cmd-V met een PNG in clipboard plaatst image-block met werkende `src` in dev portal | handmatige browser smoketest (Playwright MCP, niet CI) |
| AC-10 | Drag-drop van een lokaal `.jpg` doet hetzelfde | handmatige browser smoketest |
| AC-11 | `klai:tenant-review` CI-job groen op de diff | GH workflow |

---

## 6. Technical Approach

### Backend — `klai-portal/backend/app/api/kb_images.py`

Nieuwe route (één file, geen nieuwe modules):

```python
@router.post("/kb-images/{kb_slug}")
async def upload_kb_image(
    kb_slug: str,
    file: UploadFile,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.PERSONAL)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # @MX:ANCHOR: KB-image upload — single enforcement point for tenant-scoped writes
    # @MX:REASON: Changing the auth dep, kb_slug → org_id binding, or the SVG-reject
    #   branch breaks tenant isolation or opens an XSS path via inline SVG.
    # @MX:SPEC: SPEC-PORTAL-DOCS-IMAGE-PASTE-001
    kb = await _get_kb_or_404(kb_slug, perms.org_id, db)  # 404 on cross-tenant
    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Image too large (max 5 MB)")
    mime = ImageStore.validate_image(data)
    if not mime:
        raise HTTPException(415, "Unsupported image type")
    if mime == "image/svg+xml":
        raise HTTPException(415, "SVG uploads not supported")
    if not settings.garage_s3_endpoint:
        raise HTTPException(503, "Image storage not configured")
    ext = _ext_from_mime(mime)  # jpeg→jpg, png→png, gif→gif, webp→webp
    store = ImageStore(
        endpoint=settings.garage_s3_endpoint,
        access_key=settings.garage_s3_access_key,
        secret_key=settings.garage_s3_secret_key,
        bucket=settings.garage_kb_bucket,
    )
    result = await store.upload_image(str(perms.org_id), kb_slug, data, ext)
    logger.info(
        "kb_image_uploaded",
        org_id=perms.org_id, kb_slug=kb_slug, kb_id=kb.id,
        size=len(data), object_key=result.object_key, deduplicated=result.deduplicated,
    )
    return {"url": f"/{result.public_url.lstrip('/')}", "deduplicated": result.deduplicated}
```

Helper `_ext_from_mime` is local; geen lib-wijziging.

### Frontend — `klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx`

Twee wijzigingen:

1. Nieuwe prop `kbSlug: string` (al gepasseerd in de huidige render-keten;
   alleen toevoegen aan de `forwardRef`-props).
2. `uploadFile`-callback in `useCreateBlockNote`:

```ts
uploadFile: async (file: File): Promise<string> => {
  const fd = new FormData()
  fd.append('file', file)
  const res = await apiFetch<{ url: string }>(
    `/api/kb-images/${kbSlug}`,
    { method: 'POST', body: fd },
  )
  return res.url
},
```

`pasteHandler` blijft ongemoeid — BlockNote's default Files-paste-pad
delegeert er niet doorheen.

### Files touched (verwacht)

| File | Change |
|---|---|
| `klai-portal/backend/app/api/kb_images.py` | + `POST /kb-images/{kb_slug}`, helper `_ext_from_mime`, imports |
| `klai-portal/backend/tests/test_kb_image_upload.py` | nieuw, ~120 regels (AC-1..AC-8) |
| `klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx` | + `kbSlug` prop, + `uploadFile` callback |
| `klai-portal/frontend/src/routes/app/docs/$kbSlug/$pageId.lazy.tsx` | + `kbSlug={kbSlug}` doorgeven aan `<BlockPageEditor>` |
| `klai-portal/frontend/src/components/kb-editor/__tests__/BlockPageEditor.test.tsx` | + 1 unit test op `uploadFile` (FormData + endpoint + URL-return) |

Totaal verwacht: **~240 regels code, 1 PR, géén migrations, géén nieuwe env vars, géén nieuwe deps.**

---

## 7. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| SVG-XSS via direct URL-navigatie | Medium (afwezig CSP op read-route) | REQ-5: SVG hard reject. Connector-pad ongewijzigd; trust-boundary verschilt |
| Page-level `edit_access` omzeild (gebruiker met KB-read mag image uploaden in een KB met page-restricted edits) | Low | KB-scope dekt 95% misbruik. Page-level check zou cross-service call naar docs-app vereisen — disproportioneel voor MVP. Zie Open questions |
| Garage `put_object` faalt halverwege | Low | `ImageStore.upload_image` is content-addressed: een retry met dezelfde bytes dedupt. Geen partial-state mogelijk |
| Memory blowup bij parallel uploads | Low | 5 MB hard cap × FastAPI standaard concurrency limiet. Geen streaming-pad nodig in MVP |
| Cross-tenant kb_slug guess | Mitigated | REQ-2 + `_get_kb_or_404` — RLS-validated tenant scope, 404 niet 403 |
| Orphan images bij page-delete of image-block-delete | Known limit | Hetzelfde gat als connectors. KB-delete wist prefix. Tracked als follow-up SPEC `SPEC-PORTAL-DOCS-IMAGE-GC-001` |

---

## 8. Open Questions

Ik heb deze knopen doorgehakt op basis van het 2026-05-12 sparring-gesprek.
Mark kan ze nog overrulen op SPEC-review:

1. **SVG-reject voor user uploads — definitief?**
   Voorstel: ja, REQ-5. Alternatief is een SVG-sanitizer (bv. `nh3` of
   `bleach`) plus een strict CSP op de read-route — apart, breder SPEC.
2. **Page-level edit_access — niet in MVP?**
   Voorstel: ja, niet enforcen. Een gebruiker met KB-edit maar niet
   page-edit zou een orphan image kunnen uploaden zonder hem in een page
   te kunnen plaatsen. Lage impact. Cross-service call naar docs-app
   alleen om page-frontmatter te lezen voelt zwaar voor de blast-radius.
3. **Max size — 5 MB blijven?**
   Voorstel: ja. Past bij de lib-default + bij screenshot-volumes.
4. **Per-page image refcount + GC — aparte SPEC?**
   Voorstel: ja, dezelfde gap als bij connectors. KB-delete wist de hele
   prefix; per-page GC is wenselijk maar niet blokkerend voor deze feature.

---

## 9. Out of Scope (expliciet)

- Image-edit features (crop, rotate, focal-point).
- Resumable uploads / chunked transfer (5 MB cap maakt het overbodig).
- Avif / heic / bmp / tiff — kunnen later in `_ALLOWED_IMAGE_MIMES` als
  user-need ontstaat.
- Vervanging van de read-route response headers met CSP — dat is een
  bredere security-hardening die ook de connector-images raakt.
- E2E Playwright tests in CI — handmatige smoketest is voldoende voor
  AC-9 / AC-10 in MVP.

---

## 10. Deployment

- Geen klai-infra wijziging (env vars bestaan al voor read-route).
- Geen alembic migration.
- Geen docker-compose wijziging.
- Standaard `portal-api.yml` GitHub Action deploy. Auto-recreate via
  `compose-up.sh portal-api` na `gh run watch --exit-status`.
- Rollback: `git revert <merge-commit>` + her-deploy. State op Garage
  (eventueel reeds geüploade images) blijft staan, geen impact —
  content-addressed.

---

## 11. References

- Code:
  - [`klai-portal/backend/app/api/kb_images.py`](../../../klai-portal/backend/app/api/kb_images.py) — bestaande GET (SPEC-TI-009)
  - [`klai-libs/image-storage/klai_image_storage/storage.py`](../../../klai-libs/image-storage/klai_image_storage/storage.py) — `ImageStore`
  - [`klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx`](../../../klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx) — editor
  - [`klai-connector/app/services/sync_engine.py:715`](../../../klai-connector/app/services/sync_engine.py) — referentie-use van `ImageStore`
- Rules:
  - `.claude/rules/klai/projects/portal-security.md` — `_get_kb_or_404`, 4-cat RLS, 404-niet-403
  - `.claude/rules/klai/projects/portal-permissions.md` — `get_caller_at_least`, `UserPermissions`
  - `.claude/rules/klai/projects/knowledge.md` — image-cleanup tabel
- BlockNote `uploadFile` contract:
  - https://www.blocknotejs.org/docs/react/components/image-toolbar
  - https://www.blocknotejs.org/docs/reference/editor/paste-handling
