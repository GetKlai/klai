---
id: SPEC-KB-IMAGES-V2-001
title: KB-images v2 — clean redesign, geen legacy routes
version: 0.1.0
status: draft
created_at: 2026-05-12
owner: mark.vletter
domain: portal-knowledge
related_specs:
  - SPEC-TI-009                  # original (auth-proxied read route — broken since landing)
  - SPEC-KB-IMAGE-001            # initial Garage S3 image pipeline (connectors)
  - SPEC-KB-IMAGE-002            # content-addressed key format + ImageStore lib
  - SPEC-PORTAL-DOCS-IMAGE-PASTE-001  # the feature that exposed the 5-layer regression
replaces:
  - SPEC-TI-009                  # full replacement; old route deleted
removes_legacy:
  - GET /kb-images/{org_id}/images/{kb_slug}/{filename}
  - POST /kb-images/{kb_slug}
  - klai_image_storage.storage.PUBLIC_IMAGE_PATH_PREFIX
  - klai_image_storage.storage.ImageStore.build_public_url
---

# SPEC-KB-IMAGES-V2-001 — KB-images v2 — clean redesign, geen legacy routes

## HISTORY

| Date       | Version | Author        | Note                                                                 |
|------------|---------|---------------|----------------------------------------------------------------------|
| 2026-05-12 | 0.1.0   | mark.vletter  | Initial draft. Eliminates the 5-layer regression chain that needed 5 PRs to band-aid |

---

## 1. Goal & Outcome

### Goal

Vervang de kb-image stack (route, key-format, ImageStore-public-url, frontend-callback) door **één samenhangend ontwerp met één bron van waarheid** zodat het patroon van impliciete koppeling tussen serializer en route — dat 3 weken lang silently 404 produceerde — structureel onmogelijk wordt.

Geen oude routes parallel houden. Geen legacy compat-laag. Clean cut, de oude paths verdwijnen.

### Outcome (success measured by)

- Eén `KbImage` value-class is de **enige** plek waar de URL-shape, S3-key-shape en route-shape gedefinieerd worden. Een mismatch tussen deze drie produceert een **compile-time** fout in pyright en een **route-load-time** fout in FastAPI startup (de app weigert te booten).
- De `klai:tenant-review` workflow heeft een nieuw guard dat een mismatch tussen `KbImage.public_url(...)` en de FastAPI route-paths van `app/api/kb_images.py` detecteert en CI rood maakt.
- Een **CI-niveau E2E** Playwright-test in de portal-frontend `e2e/prod-tenant/` pakket: log in, navigate naar een KB-page, fetch een productie-URL-shape via `<img>` element en assert dat `naturalWidth > 0`. Loopt op elke `klai-portal/backend/app/api/kb_images.py` of `klai_image_storage/**` change.
- De 1553 bestaande Voys + Klai-help productie-images blijven werken **zonder dat een rij in `knowledge.artifact_images` of een S3-object hoeft te worden aangeraakt** (zelfde S3 key prefix, zelfde URL pad). De change is een refactor van de **codebase**, niet van de data.
- De docs-editor paste-flow (SPEC-PORTAL-DOCS-IMAGE-PASTE-001) blijft werken bit-voor-bit identiek voor de eindgebruiker, alleen achter de UI-laag is de stack vereenvoudigd.
- **Zero legacy routes**: `GET /kb-images/{org_id}/{kb_slug}/{filename}` (4-segment) bestaat niet meer in v2. Niet als deprecated-warning route, niet als 301-redirect. Helemaal weg.

### Non-goal

- Image-edit features (crop, rotate, focal-point).
- Resumable uploads / chunked transfer (5 MB cap blijft).
- Per-page image refcount + automatic GC (eigen SPEC, SPEC-PORTAL-DOCS-IMAGE-GC-001).
- Migratie naar een ander object-store (Cloudflare R2, etc.).
- Image-rendering optimalisaties (responsive srcset, AVIF, etc.).

---

## 2. Background

### De 5-laagse regression chain (waarom een rewrite, niet meer patches)

SPEC-TI-009 (landed 2026-04-22) introduceerde de auth-proxied read-route op `/kb-images/...`. Tot 2026-05-12 — de dag dat SPEC-PORTAL-DOCS-IMAGE-PASTE-001 de eerste browser-leg consument shipte — was de route **silently broken** door vijf gestapelde bugs:

| # | Layer | Symptom | Cause | Fix PR |
|---|---|---|---|---|
| 1 | Caddy | 502 Bad Gateway | `reverse_proxy portal-api:8000` (dead port; portal-api listens op :8010) | #598 |
| 2 | Caddy | 404 (na #1) | `handle_path /kb-images/*` strip't de `/kb-images/` prefix; FastAPI ziet pad zonder prefix | #598 |
| 3 | portal-api | 403 (na #1+#2) | Auth-check vergeleek `session.org_id` (portal int) tegen path-`org_id` (typed int) terwijl S3-keys zitadel-string-prefix gebruiken | #598 |
| 4 | docker-compose | 503 (na #1+#2+#3) | `portal-api` env block miste alle `GARAGE_S3_*` vars (SPEC-SEC-ENVFILE-SCOPE-001 had ze nooit toegevoegd) | #600 |
| 5 | portal-api | 404 op route-niveau (na #1+#2+#3+#4) | Route declaration `/kb-images/{org_id}/{kb_slug}/{filename}` (4 placeholders) vs. URL-shape `/kb-images/{org}/images/{kb}/{sha}.ext` (5 segments) — geen FastAPI match | #607 |
| (frontend) | portal-frontend | Eternal "Loading..." | `uploadFile` callback POSTed naar `/api/kb-images/{kbSlug}`, route bestaat op `/kb-images/{kbSlug}` | #602 |

Elke fix onthulde de volgende laag. **Zes patch-PRs in één werkdag** om iets dat in productie nooit gewerkt had te laten functioneren. Het patroon is structureel — de root cause is dat **drie verschillende files** (klai_image_storage's `build_public_url`, kb_images.py's route declaration, BlockPageEditor.tsx's fetch URL) onafhankelijk de URL-shape vaststelden. Drift was onvermijdelijk.

### Waarom geen "deprecation period"

Een v1+v2 parallel route bestaat normaal om callers tijd te geven te migreren. Hier zijn er **geen externe callers**:
- KB-image URLs leven alleen in (a) page-content markdown en (b) connector-image-references in `knowledge.artifact_images`
- Beide blijven werken zolang het S3-key-pad en het public-URL-pad **identiek blijven**
- v2 raakt dat pad niet — alleen hoe de codebase het intern representeert

Geen externe API-consumer is afhankelijk van de oude representatie. Een "deprecation phase" zou alleen meer code-paths produceren die we later weer moeten opruimen.

---

## 3. Scope

| In | Out |
|---|---|
| Nieuwe `app/core/kb_image_url.py` module met `KbImage` value-class | Wijziging aan `knowledge.artifact_images` schema of data |
| Refactor `app/api/kb_images.py` om volledig via `KbImage` te werken (route paths, response URLs) | Wijziging aan klai-connector / klai-knowledge-ingest write-pad |
| Verwijdering van `klai_image_storage.storage.PUBLIC_IMAGE_PATH_PREFIX` + `build_public_url()` | Aanpassing van bestaande S3-keys |
| Refactor `klai_image_storage.storage.ImageStore` om geen URL-shape kennis meer te hebben (alleen S3-key shape via `build_object_key`) | Wijziging Garage bucket-conventie |
| Update klai-connector `_upload_images` en klai-knowledge-ingest crawler om `KbImage.from_components()` te gebruiken voor URL-generation | Image-edit features (crop, etc.) |
| Update `BlockPageEditor.tsx` `uploadFile` callback om de POST URL via een geëxporteerde `KB_IMAGE_UPLOAD_PATH` constant te bouwen | Image lifecycle / GC |
| FastAPI startup-time assertion: alle `kb_images.py` routes komen exact overeen met `KbImage`-gegenereerde paths | Multi-region S3 |
| `klai:tenant-review` workflow: ast-grep guard tegen `/kb-images/` literal hardcoded buiten `app/core/kb_image_url.py` | Resumable upload |
| Playwright E2E test in `e2e/prod-tenant/` die de echte production URL-shape fetcht via `<img>` en `naturalWidth` checkt | Image preview thumbnails |
| Verwijdering van het oude pitfall-entry `caddy-proxy-route-without-browser-leg` (vervangen door een nieuwe entry over single-source-of-truth voor URL-shapes) | |

---

## 4. EARS Requirements

### REQ-1 — Single source of truth: `KbImage` value-class

**When** de codebase een kb-image URL of S3-key wil bouwen, **the system** MUST dat uitsluitend doen via een nieuwe `app/core/kb_image_url.py::KbImage` value-class met deze API:

```python
@dataclass(frozen=True)
class KbImage:
    zitadel_org_id: str
    kb_slug: str
    sha256: str
    ext: str  # one of: jpg, png, gif, webp

    @classmethod
    def from_bytes(cls, zitadel_org_id: str, kb_slug: str, data: bytes, mime: str) -> "KbImage": ...

    @classmethod
    def from_path(cls, path: str) -> "KbImage | None":
        """Parse a path produced by .public_path() back into a KbImage.
        Returns None if the path doesn't match the canonical shape — used
        by tests + the route-load-time assertion."""
        ...

    @property
    def s3_key(self) -> str:
        return f"{self.zitadel_org_id}/images/{self.kb_slug}/{self.sha256}.{self.ext}"

    @property
    def public_path(self) -> str:
        """Relative URL path served by the kb-images auth-proxy."""
        return f"/kb-images/{self.zitadel_org_id}/images/{self.kb_slug}/{self.sha256}.{self.ext}"

    # FastAPI route template — exposed for the route declaration AND the
    # startup-time match assertion.
    ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{zitadel_org_id}/images/{kb_slug}/{filename}"
    UPLOAD_ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{kb_slug}"
```

`ROUTE_TEMPLATE` is de **enige** plek waar de FastAPI-route-string mag worden gedeclareerd. `kb_images.py` importeert hem letterlijk.

### REQ-2 — Route declaration via constant, niet string literal

**When** `app/api/kb_images.py` z'n routes declareert, **the system** MUST `KbImage.ROUTE_TEMPLATE` en `KbImage.UPLOAD_ROUTE_TEMPLATE` als constanten gebruiken in de `@router.get(...)` en `@router.post(...)` decorators. **Forbidden**: een hardcoded string-literal `/kb-images/...` in een decorator.

### REQ-3 — Route-load-time match assertion (defense in depth)

**When** portal-api boot, **the system** MUST in z'n FastAPI lifespan startup-hook een assertie uitvoeren die `KbImage(zitadel_org_id="X", kb_slug="Y", sha256="Z", ext="png").public_path` parseert via `KbImage.from_path(...)` én tegelijk verifieert dat het pad mapped op de zelfde route-template via een FastAPI router-introspection. Als de twee niet overeenkomen, **the system** MUST de boot afbreken met een leesbare error (`KbImage URL-shape vs route-template drift gedetecteerd`).

### REQ-4 — Verwijderen van `ImageStore.build_public_url` + `PUBLIC_IMAGE_PATH_PREFIX`

**When** v2 ge-mergeed is, **the system** MUST `klai_image_storage.storage.PUBLIC_IMAGE_PATH_PREFIX` (constante) en `klai_image_storage.storage.ImageStore.build_public_url()` (method) niet meer bevatten. Beide worden verwijderd. Callers in klai-connector en klai-knowledge-ingest worden gemigreerd naar `KbImage.from_components(...).public_path`.

### REQ-5 — ast-grep CI guard: geen hardcoded `/kb-images/` paths

**When** een PR `/kb-images/` als string-literal toevoegt buiten `app/core/kb_image_url.py` (de single source) of `klai-portal/frontend/src/lib/kb_image_url.ts` (de TS-mirror, indien nodig), **the system** MUST in CI rood worden via een nieuwe ast-grep rule `no-hardcoded-kb-image-path.yml`.

### REQ-6 — Playwright E2E in CI

**When** een PR `klai-portal/backend/app/api/kb_images.py`, `klai-portal/backend/app/core/kb_image_url.py`, `klai-libs/image-storage/**`, of `klai-portal/frontend/src/components/kb-editor/**` raakt, **the system** MUST in CI een Playwright-test draaien die:

1. Inlogt op `https://prod-or-staging.getklai.com` via storage-state
2. Een POST doet naar `/kb-images/{test-kb-slug}` met een 1x1 PNG
3. De teruggekregen `url` als `<img src>` rendert
4. Asserteert dat `img.naturalWidth > 0` en `img.complete === true`

Geen mock. Geen TestClient. Geen unit-test substitute. Het hele Caddy → portal-api → Garage pad moet door de test heen.

### REQ-7 — Frontend uploadFile uit dezelfde bron

**When** `BlockPageEditor.tsx` z'n upload-URL bouwt, **the system** MUST dat doen via een geëxporteerde TypeScript-constante in `klai-portal/frontend/src/lib/kb-image-url.ts` (de exact equivalent van REQ-1's Python module), niet via een inline string-literal. De TS-module heeft een unit test die de URL-shape vergelijkt met een fixture-string en faalt als ze driften.

### REQ-8 — Geen legacy routes blijven achter

**When** v2 ge-mergeed is, **the system** MUST géén route hebben op een ander pad dan REQ-1's `ROUTE_TEMPLATE`. Een grep voor `kb-images/` in de Caddy file, in `kb_images.py`, in `BlockPageEditor.tsx`, en in `process-rules.md` mag alleen verwijzingen naar `KbImage.ROUTE_TEMPLATE` opleveren of een commentaar over deze SPEC. Geen 4-segment route. Geen 301-redirect. Geen `/api/kb-images/...` shadow-route.

### REQ-9 — Pitfall-entry geupdate

**When** v2 ge-mergeed is, **the system** MUST in `.claude/rules/klai/pitfalls/process-rules.md` de huidige `caddy-proxy-route-without-browser-leg` entry vervangen door een nieuwe entry die het complete patroon documenteert: "drie onafhankelijke files die dezelfde URL-shape hardcoderen → silent drift → 5-layer regression". Concrete prevention: REQ-1's single-source pattern + REQ-3's boot-time assertion + REQ-6's CI E2E.

---

## 5. Acceptance Criteria

| # | Criterium | Verificatie |
|---|---|---|
| AC-1 | `KbImage(zitadel_org_id, kb_slug, sha256, ext).public_path` returnt exact dezelfde 5-segment URL als de huidige productie-shape op de 1553 Voys + Klai-help images | pytest fixture met 10 productie-keys; round-trip via `KbImage.from_path` en assert == |
| AC-2 | Pyright vindt geen `/kb-images/` string-literals buiten `kb_image_url.py` | ast-grep CI guard (REQ-5) |
| AC-3 | Portal-api lifespan-assertion vuurt en boot crash't als ik de route declaration handmatig drift maak (sabotage test: rename `images` → `imgs` in `kb_images.py`) | pytest test_route_template_drift_aborts_boot |
| AC-4 | Bestaande Voys connector image laadt 200 via browser-fetch via Playwright | Playwright E2E in `e2e/prod-tenant/` |
| AC-5 | Mark plakt screenshot in docs-editor → image verschijnt → page reload toont image | Playwright E2E |
| AC-6 | klai-connector `_upload_images` gebruikt `KbImage.from_components(...)` ipv `ImageStore.build_public_url(...)` | Code review + grep |
| AC-7 | klai-knowledge-ingest `download_and_upload_crawl_images` gebruikt zelfde | Code review + grep |
| AC-8 | `klai_image_storage.storage` heeft géén `PUBLIC_IMAGE_PATH_PREFIX` of `build_public_url` meer | pytest fixture probeert import — moet ImportError raisen |
| AC-9 | Caddy file heeft géén `handle_path` block meer voor `/kb-images/*` (alleen `handle`) | grep |
| AC-10 | `klai:tenant-review` workflow guard rule loaded + actief op PR | gh actions workflow inspection |
| AC-11 | Geen 5xx of 404 op `/kb-images/*` in VictoriaLogs van de eerste 24u na deploy | Grafana panel + alert |

---

## 6. Technical Approach

### Backend

**Nieuw**: `klai-portal/backend/app/core/kb_image_url.py` — de single source of truth.

```python
from dataclasses import dataclass
from typing import ClassVar
import hashlib
import re

_MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg", "image/png": "png",
    "image/gif": "gif", "image/webp": "webp",
}
_VALID_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_KB_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VALID_ZITADEL_RE = re.compile(r"^[0-9]{1,20}$")
_VALID_EXT_RE = re.compile(r"^(jpg|png|gif|webp)$")


@dataclass(frozen=True, slots=True)
class KbImage:
    zitadel_org_id: str
    kb_slug: str
    sha256: str
    ext: str

    ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{zitadel_org_id}/images/{kb_slug}/{filename}"
    UPLOAD_ROUTE_TEMPLATE: ClassVar[str] = "/kb-images/{kb_slug}"
    _PATH_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^/kb-images/(?P<zitadel_org_id>[0-9]{1,20})"
        r"/images/(?P<kb_slug>[a-z0-9][a-z0-9-]{0,63})"
        r"/(?P<sha256>[0-9a-f]{64})\.(?P<ext>jpg|png|gif|webp)$"
    )

    def __post_init__(self) -> None:
        if not _VALID_ZITADEL_RE.match(self.zitadel_org_id):
            raise ValueError(f"invalid zitadel_org_id: {self.zitadel_org_id!r}")
        if not _VALID_KB_SLUG_RE.match(self.kb_slug):
            raise ValueError(f"invalid kb_slug: {self.kb_slug!r}")
        if not _VALID_SHA256_RE.match(self.sha256):
            raise ValueError(f"invalid sha256: {self.sha256!r}")
        if not _VALID_EXT_RE.match(self.ext):
            raise ValueError(f"invalid ext: {self.ext!r}")

    @classmethod
    def from_bytes(cls, zitadel_org_id: str, kb_slug: str, data: bytes, mime: str) -> "KbImage":
        ext = _MIME_EXT.get(mime)
        if ext is None:
            raise ValueError(f"unsupported MIME: {mime!r}")
        return cls(
            zitadel_org_id=zitadel_org_id,
            kb_slug=kb_slug,
            sha256=hashlib.sha256(data).hexdigest(),
            ext=ext,
        )

    @classmethod
    def from_path(cls, path: str) -> "KbImage | None":
        m = cls._PATH_RE.match(path)
        if m is None:
            return None
        return cls(**m.groupdict())

    @property
    def s3_key(self) -> str:
        return f"{self.zitadel_org_id}/images/{self.kb_slug}/{self.sha256}.{self.ext}"

    @property
    def public_path(self) -> str:
        return f"/kb-images/{self.s3_key}"
```

**Refactor**: `app/api/kb_images.py`.

```python
from app.core.kb_image_url import KbImage

@router.get(KbImage.ROUTE_TEMPLATE)
async def get_kb_image(...): ...

@router.post(KbImage.UPLOAD_ROUTE_TEMPLATE)
async def upload_kb_image(...): ...
```

**Lifespan assertion** in `app/main.py`:

```python
def _assert_kb_image_routes_match_value_class(app: FastAPI) -> None:
    """REQ-3: fail boot if route declarations drift from KbImage.ROUTE_TEMPLATE."""
    from app.core.kb_image_url import KbImage

    declared_paths = {route.path for route in app.routes if "kb-images" in getattr(route, "path", "")}
    expected = {KbImage.ROUTE_TEMPLATE, KbImage.UPLOAD_ROUTE_TEMPLATE}
    if declared_paths != expected:
        raise RuntimeError(
            f"KbImage URL-shape vs route-template drift detected: "
            f"declared={declared_paths} expected={expected}"
        )
```

### Library refactor

**`klai-libs/image-storage/klai_image_storage/storage.py`**: schrap `PUBLIC_IMAGE_PATH_PREFIX` en `ImageStore.build_public_url`. `ImageStore.build_object_key` blijft, want hij is content-addressed en doet alleen S3-key logica.

Callers (klai-connector + klai-knowledge-ingest) krijgen een dunne adapter die voor hun specifieke org-id pattern de `KbImage` construeert.

### Frontend

**Nieuw**: `klai-portal/frontend/src/lib/kb-image-url.ts` — TS-mirror van REQ-1.

```typescript
export const KB_IMAGE_UPLOAD_PATH = (kbSlug: string) => `/kb-images/${encodeURIComponent(kbSlug)}`;
export const KB_IMAGE_PUBLIC_PATH_RE = /^\/kb-images\/[0-9]{1,20}\/images\/[a-z0-9][a-z0-9-]{0,63}\/[0-9a-f]{64}\.(jpg|png|gif|webp)$/;
```

**Refactor `BlockPageEditor.tsx`**:

```typescript
import { KB_IMAGE_UPLOAD_PATH } from '@/lib/kb-image-url';
...
const res = await apiFetch<{...}>(KB_IMAGE_UPLOAD_PATH(kbSlug), {method:'POST', body:fd});
```

**Unit test** vergelijkt de TS-output met een fixture string die identiek is aan een Python-genereerde URL — voorkomt cross-language drift.

### CI guards

**`rules/no-hardcoded-kb-image-path.yml`** (ast-grep):

```yaml
id: no-hardcoded-kb-image-path
language: python
rule:
  pattern: $STR
  constraints:
    STR:
      regex: '"/kb-images/'
files:
  include:
    - "klai-portal/backend/**/*.py"
  exclude:
    - "klai-portal/backend/app/core/kb_image_url.py"
    - "klai-portal/backend/tests/**"
```

Plus een TypeScript-equivalent voor de frontend.

**Playwright E2E** in `klai-portal/frontend/e2e/kb-images.spec.ts`:

```typescript
test('kb-image upload + render', async ({ page, context }) => {
  await page.goto(STAGING_URL);
  // ... use storage-state to skip login ...
  const png = readFileSync('e2e/fixtures/1x1.png');
  const response = await page.request.post('/kb-images/e2e-test-kb', {
    multipart: { file: { name: '1x1.png', mimeType: 'image/png', buffer: png } },
  });
  expect(response.status()).toBe(200);
  const { url } = await response.json();

  await page.setContent(`<img id="t" src="${url}" />`);
  await page.locator('#t').waitFor({ state: 'visible' });
  const dims = await page.locator('#t').evaluate((img: HTMLImageElement) => ({
    nw: img.naturalWidth, complete: img.complete,
  }));
  expect(dims.nw).toBeGreaterThan(0);
  expect(dims.complete).toBe(true);
});
```

Runs op elke PR die `kb_images.py`, `kb_image_url.py`, `klai-libs/image-storage/**` of `BlockPageEditor.tsx` raakt.

### Files touched

| File | Change |
|---|---|
| `klai-portal/backend/app/core/kb_image_url.py` | **new** — single source |
| `klai-portal/backend/app/api/kb_images.py` | refactor — import + use `KbImage` |
| `klai-portal/backend/app/main.py` | add lifespan assertion (REQ-3) |
| `klai-portal/backend/tests/test_kb_image_url.py` | **new** — round-trip + production fixture |
| `klai-portal/backend/tests/test_kb_images_*.py` | updated to use `KbImage.from_components(...).public_path` |
| `klai-libs/image-storage/klai_image_storage/storage.py` | **remove** `PUBLIC_IMAGE_PATH_PREFIX` + `build_public_url` |
| `klai-libs/image-storage/tests/test_storage.py` | update — remove tests for deleted API |
| `klai-connector/app/services/sync_engine.py` | refactor — use `KbImage` for URL generation in image-references |
| `klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py` | idem |
| `klai-portal/frontend/src/lib/kb-image-url.ts` | **new** — TS mirror |
| `klai-portal/frontend/src/components/kb-editor/BlockPageEditor.tsx` | import + use `KB_IMAGE_UPLOAD_PATH` |
| `klai-portal/frontend/e2e/kb-images.spec.ts` | **new** — Playwright E2E |
| `klai-portal/frontend/src/lib/__tests__/kb-image-url.test.ts` | **new** — TS-vs-Python drift test |
| `rules/no-hardcoded-kb-image-path.yml` | **new** — ast-grep guard |
| `.github/workflows/portal-api.yml` | add ast-grep rule to existing check |
| `.github/workflows/portal-frontend.yml` | add Playwright E2E job on path-trigger |
| `.claude/rules/klai/pitfalls/process-rules.md` | **replace** `caddy-proxy-route-without-browser-leg` with the new pattern |
| `deploy/caddy/Caddyfile` | no change (current `handle /kb-images/*` is correct) |

Totaal verwacht: **~600-800 regels code over backend + frontend + libs + tests + CI**, één samenhangende PR.

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Bestaande page-content markdown bevat de oude URL-shape | URL-shape is **identiek** voor end-users (5 segments); alleen interne codepath verandert |
| Cross-service refactor van klai_image_storage breekt connector + ingest | E2E test (REQ-6) raakt het hele pad; CI rood voor merge |
| TS-mirror van Python `KbImage` drift over tijd | Unit-test vergelijkt outputs met identical fixture (REQ-7); CI rood bij drift |
| Lifespan-assertion zelf bevat een bug | pytest `test_route_template_drift_aborts_boot` test de assertion via sabotage |
| Playwright E2E flaky in CI | Storage-state seeded one-time; test uses `page.request.*` (not browser nav) for the POST; only the `<img>` render is browser-side. Acceptabel flake-budget: 1 in 100. |
| ast-grep rule te strict (false positives) | Tests dir + `kb_image_url.py` whitelisted; comments in pitfall-rules zijn markdown, niet Python source |

---

## 8. Out of Scope (expliciet)

- Een SPEC-KB-IMAGES-V3 voor S3 → R2 migration
- Refactor van Caddyfile (mogelijk in opvolgende SPEC)
- Image transformatie pipeline (resize, format conversion)
- CDN voor kb-images
- Per-page GC van orphaned images (eigen SPEC `SPEC-PORTAL-DOCS-IMAGE-GC-001`)
- Image-rendering in chat-citations (LLM markdown-image embedding — vereist prompt + retrieval-API werk)

---

## 9. Deployment

- **Geen** alembic-migratie nodig (S3-keys + DB-rijen blijven onveranderd).
- **Geen** klai-infra of SOPS-wijziging.
- Standaard portal-api + portal-frontend deploy workflows.
- Rollback: `git revert <merge-commit>`. Geen data-impact (alleen interne codestructuur).
- Verificatie: Playwright E2E moet groen zijn op de eerste post-deploy CI run. Manueel: de huidige bekende productie image-URLs (Voys `dae543ab...`, Klai-help `71e67cdc...`) moeten 200 returnen via curl met session-cookie.

---

## 10. Open Questions (voor SPEC-review)

1. **TypeScript mirror — code-gen of handmatig?** Een codegen van Python-`KbImage` naar TS via een script-in-CI zou drift onmogelijk maken, maar adds complexity. Handmatig + drift-test is goedkoper. Voorstel: handmatig + test; gen kan in een latere SPEC.
2. **Lifespan-assertion fail-mode** — fail-loud (`raise RuntimeError`) of fail-soft (`logger.error` + boot doorgaan)? Voorstel: fail-loud. Een drift hier is structureel; door laten gaan = silent breakage opnieuw.
3. **Caddy `handle` block** — laten staan zoals nu of refactoren naar een named matcher voor consistentie met andere blocks? Voorstel: laten staan. Caddy-niveau is niet de bron van het probleem.
4. **`KbImage.UPLOAD_ROUTE_TEMPLATE`** zou eigenlijk niet hetzelfde object zijn als de read-route — verschillende shapes. Voorstel: aparte `KbImageUpload` of een NamedTuple van twee templates. Te discussiëren bij implementation.

---

## 11. References

- [SPEC-TI-009 / SPEC-KB-IMAGE-002](../SPEC-TI-009/) — original
- [SPEC-PORTAL-DOCS-IMAGE-PASTE-001](../SPEC-PORTAL-DOCS-IMAGE-PASTE-001/spec.md) — the feature that exposed the chain
- PRs in the 2026-05-12 regression chain: [#598](https://github.com/GetKlai/klai/pull/598), [#600](https://github.com/GetKlai/klai/pull/600), [#602](https://github.com/GetKlai/klai/pull/602), [#607](https://github.com/GetKlai/klai/pull/607)
- [.claude/rules/klai/pitfalls/process-rules.md](../../.claude/rules/klai/pitfalls/process-rules.md) — current pitfall entry (to be replaced)
