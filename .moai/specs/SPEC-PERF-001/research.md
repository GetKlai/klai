# Research: SPEC-PERF-001 — Frontend Observability Stack Evaluatie

## Onderzoeksvraag

Moet Klai migreren van GlitchTip (@sentry/react) naar Grafana Faro voor frontend error tracking en performance monitoring? Of is de huidige stack (GlitchTip + web-vitals via Prometheus) de betere keuze?

## Bias Disclosure

De eigenaar heeft langdurige ervaring met Sentry, wat een bias richting GlitchTip behouden oplevert. Dit onderzoek vergelijkt op feiten, niet op bekendheid.

---

## 1. Huidige Stack Inventaris

### GlitchTip Containers (deploy/docker-compose.yml)

| Container | Image | Netwerken | Doel |
|-----------|-------|-----------|------|
| `glitchtip-web` | `glitchtip/glitchtip:latest` | klai-net, net-postgres, net-redis | Django web app (port 8000) |
| `glitchtip-worker` | `glitchtip/glitchtip:latest` | net-postgres, net-redis | Celery worker + beat |
| `glitchtip-migrate` | `glitchtip/glitchtip:latest` | net-postgres | One-shot migration (exits) |

Gedeelde infra: postgres (pgvector/pgvector:pg18) en redis (redis:alpine) — beide al aanwezig voor andere services.

GlitchTip v6 (feb 2026) introduceert all-in-one mode: 1 container vervangt web + worker. Minimaal: **2 containers** (all-in-one + postgres).

Geheugen: 256 MB - 1 GB (vs. Sentry self-hosted: 16 GB+, 40+ containers).

### Frontend SDK Integratie

**klai-portal/frontend/src/main.tsx (lines 53-77):**
- `@sentry/react ^10.43.0` — DSN met hyphens-strip fix voor GlitchTip
- Integrations: `tanstackRouterBrowserTracingIntegration`, `consoleLoggingIntegration`, `breadcrumbsIntegration`
- `tracesSampleRate: 0.05`, `sendDefaultPii: false`, IP stripping in `beforeSend`
- `Sentry.captureException()` in TanStack Query mutation error handler

**klai-portal/frontend/src/lib/logger.ts:**
- 9 tagged consola loggers met `Sentry.createConsolaReporter()` in productie
- warn/error logs vloeien automatisch naar GlitchTip

**klai-portal/frontend/vite.config.ts (lines 20-29):**
- `sentryVitePlugin` uploadt source maps naar `https://errors.getklai.com`
- Maps worden verwijderd na upload (`filesToDeleteAfterUpload`)

**package.json:**
- `@sentry/react: ^10.43.0` (runtime)
- `@sentry/vite-plugin: ^5.1.1` (build-time)

**Backend:** Geen Sentry SDK. Backend errors gaan via VictoriaLogs naar Grafana.

### Grafana Monitoring Stack

- Alloy: scrapes metrics (node, retrieval-api, cAdvisor) + verzamelt Docker logs
- VictoriaMetrics: time-series metrics, Prometheus-compatible, 30d retention
- VictoriaLogs: log aggregation, Loki push API compatible, 30d retention
- Grafana: dashboards + alerting, provisioned via JSON

---

## 2. Grafana Faro SDK Analyse

### Capabilities

| Feature | Faro Web SDK | @sentry/react |
|---------|-------------|---------------|
| Error capture (uncaught exceptions) | Ja | Ja |
| Console log capture | Ja (`captureConsole: true`) | Ja (`consoleLoggingIntegration`) |
| Web Vitals (LCP, FCP, INP, CLS, TTFB) | Ja (automatisch) | Nee (basic transaction timing) |
| Browser tracing | Ja (via `@grafana/faro-web-tracing`, OTel-gebaseerd) | Ja (via `browserTracingIntegration`) |
| React Router integration | Ja (React Router v6/v7 via `@grafana/faro-react`) | Ja (alle major routers) |
| **TanStack Router integration** | **Nee** — alleen React Router v6/v7 | **Ja** — `tanstackRouterBrowserTracingIntegration` |
| Session context | Ja (session ID, user metadata) | Ja (scope, user, tags) |
| Source map deobfuscation | Via Alloy faro.receiver (filesystem-based) | Via upload API (sentry-cli / Vite plugin) |
| Breadcrumbs | Beperkt (logs, niet DOM clicks) | Ja (DOM, console, fetch, history) |

### Bundle Size

| Package | Min+Gzip (geschat) | Rol |
|---------|-------------------|-----|
| `@grafana/faro-web-sdk` | ~15-20 kB | Core SDK + web vitals |
| `@grafana/faro-web-tracing` | ~30-40 kB | OTel tracing (optioneel) |
| `@grafana/faro-react` | ~3-5 kB | React integratie |
| **Faro totaal (zonder tracing)** | **~18-25 kB** | |
| **Faro totaal (met tracing)** | **~50-65 kB** | |
| `@sentry/react` | **~30 kB** | Alles-in-een |

Netto bundle impact bij migratie: **klein** (-30 kB Sentry + ~18-25 kB Faro = ~5-12 kB besparing zonder tracing). Met tracing: **groter** (+20-35 kB).

### Kritieke Bevinding: Geen TanStack Router Support

Klai's portal gebruikt TanStack Router, niet React Router. Faro's `@grafana/faro-react` biedt alleen `FaroRoutes` en `createReactRouterV6Options` — er is **geen TanStack Router integratie**. Dit betekent:

- Geen automatische route change tracking
- Handmatige instrumentatie vereist voor page navigation
- Verlies van de huidige `tanstackRouterBrowserTracingIntegration` die out-of-the-box werkt

---

## 3. Alloy faro.receiver Analyse

### Architectuur

```
Browser (Faro SDK) → HTTP POST /collect → Alloy faro.receiver
                                            ├── output.logs → Loki/VictoriaLogs
                                            └── output.traces → OTel/Tempo
```

### Kritieke Bevinding: Geen Metrics Output

De `faro.receiver` component heeft **alleen** `logs` en `traces` outputs:

```alloy
faro.receiver "default" {
    output {
        logs   = [loki.write.default.receiver]    // LogsReceiver
        traces = [otelcol.exporter.otlphttp.input] // otelcol.Consumer
    }
    // GEEN metrics output
}
```

Dit betekent:
- Web Vitals data gaat als **log lines** naar VictoriaLogs, niet als Prometheus histograms
- **Geen `histogram_quantile()`** voor p50/p95 berekeningen — de standaard voor performance monitoring
- Je moet LogQL/VictoriaLogs queries schrijven om numerieke waarden uit logs te parsen
- Dashboard queries worden significant complexer en minder performant

### VictoriaLogs Loki Compatibiliteit

VictoriaLogs accepteert Loki push format op `/insert/loki/api/v1/push`. Alloy's `loki.write` component kan hier naartoe schrijven. De routing is technisch mogelijk:

```alloy
loki.write "victorialogs" {
    endpoint {
        url = "http://victorialogs:9428/insert/loki/api/v1/push"
    }
}
```

### Source Map Handling

- Source maps worden gelezen van het filesystem of gedownload via HTTP
- **Geen upload API** voor self-hosted — maps moeten beschikbaar zijn op disk of via URL
- **Bekende regressie in Alloy 1.8.0+**: [GitHub issue #3608](https://github.com/grafana/alloy/issues/3608) — filesystem-based source maps breken
- Vereist een volume mount met source maps naar de Alloy container

### Traces

- Traces routeren naar OTel-compatible backend (Tempo)
- Klai draait **geen Tempo** — zou een nieuw component toevoegen
- Traces zijn optioneel in de faro.receiver config

---

## 4. Error Tracking: Self-Hosted Faro vs GlitchTip

Dit is de meest kritieke vergelijking.

### Error Grouping

| Aspect | GlitchTip | Self-hosted Faro |
|--------|-----------|-----------------|
| Automatische error grouping | Ja (fingerprint-based) | **Nee** — errors zijn raw log lines in Loki/VictoriaLogs |
| "Issues" view (groepering, count, first/last seen) | Ja (built-in UI) | **Nee** — bouw je eigen dashboard in Grafana met LogQL |
| Issue management (resolve, ignore, assign) | Ja | **Nee** |
| Error alerts | Ja (email, webhook) | Via Grafana alerting op log queries |
| Custom fingerprinting | Ja (scope.fingerprint) | N.v.t. |

### Source Map Vergelijking

| Aspect | GlitchTip | Self-hosted Faro |
|--------|-----------|-----------------|
| Upload methode | sentry-cli / Vite plugin / API | Filesystem mount / HTTP download |
| Debug ID support | Ja (v4.2+) | Ja |
| Bekende problemen | "Lots of bugs, no contributors" (epic #7) | Alloy 1.8.0+ regressie (#3608) |
| CI/CD integratie | sentryVitePlugin (huidige setup) | Faro bundler plugins (Vite support) |

Beide hebben source map problemen. GlitchTip's zijn gedocumenteerd maar persistent. Faro's zijn recenter en actief gerepareerd door Grafana Labs.

### Grafana Cloud vs Self-Hosted Faro

De features die Faro competitief maken met Sentry/GlitchTip — **automatische error grouping, source map deobfuscation UI, error overview dashboard** — zijn **Grafana Cloud features**. Ze zijn niet beschikbaar in de self-hosted open-source stack.

Self-hosted Faro is fundamenteel een **telemetry collection SDK + Alloy receiver**. Het is geen error tracking platform.

---

## 5. Onderhoudslast Vergelijking

### GlitchTip Behouden (huidige situatie)

| Component | Status | Onderhoud |
|-----------|--------|-----------|
| GlitchTip containers (2-4) | Draait | Image updates elke 2-4 maanden |
| @sentry/react SDK | Werkt | npm update |
| sentryVitePlugin | Werkt | Automatisch via CI |
| GlitchTip postgres DB | Gedeeld | Migrations bij updates |
| errors.getklai.com subdomain | Geconfigureerd | Nul onderhoud |

Extra voor Web Vitals (SPEC-PERF-001 bestaand plan):
- web-vitals lib (~1.5 kB)
- POST /api/vitals endpoint
- Alloy scrape block (5 regels config)
- Grafana dashboard JSON

### Migratie naar Faro

| Component | Wijziging | Onderhoud |
|-----------|-----------|-----------|
| GlitchTip containers verwijderen | -2 tot -4 containers | — |
| @sentry/react verwijderen | SDK swap | — |
| @grafana/faro-web-sdk toevoegen | Nieuw SDK | npm update |
| Alloy faro.receiver configureren | Nieuwe config blok + source map setup | Config onderhoud |
| Source map pipeline herbouwen | Volume mount of HTTP download in Alloy | Complexer dan huidige Vite plugin |
| Error tracking dashboards bouwen | Custom Grafana dashboards met LogQL | **Doorlopend onderhoud** — elke error class vereist dashboard updates |
| Error alerting herbouwen | Grafana alerting op log queries | Log query onderhoud |
| consola → Sentry reporter vervangen | Nieuwe transport schrijven | Custom code onderhoud |
| TanStack Router instrumentatie | Handmatig schrijven (geen Faro support) | Custom code onderhoud |
| TanStack Query error handling | Pattern vervangen (captureException → pushError) | Eenmalig |

**Netto**: GlitchTip verwijderen bespaart 2-4 containers (~256 MB-1 GB RAM), maar voegt toe:
- Significant complexere Alloy configuratie
- Custom error tracking dashboards (vervangen van GlitchTip's built-in UI)
- Custom error alerting
- Custom TanStack Router integratie
- Custom source map pipeline

---

## 6. Migratie Pad Analyse

### Stapsgewijze migratie (Faro naast GlitchTip)?

Technisch mogelijk: Faro en @sentry/react kunnen naast elkaar draaien. Maar:
- Dubbele error reporting (elke error naar GlitchTip EN naar VictoriaLogs)
- Dubbele bundle cost (~30 kB + ~18-25 kB)
- Verwarring over welk systeem "de waarheid" is
- Geen duidelijk rollback moment

### Error history

Bij migratie naar Faro gaat **alle error history** in GlitchTip verloren. Errors in VictoriaLogs hebben 30d retention. Er is geen import/export pad.

### Frontend code wijzigingen

| File | Wijziging |
|------|-----------|
| `main.tsx` | Verwijder Sentry.init, voeg initializeFaro toe |
| `lib/logger.ts` | Verwijder createConsolaReporter, schrijf Faro transport |
| `vite.config.ts` | Verwijder sentryVitePlugin, voeg Faro bundler plugin toe |
| Alle files met `Sentry.captureException` | Vervang door `faro.api.pushError` |
| package.json | Verwijder @sentry/*, voeg @grafana/faro-* toe |
| `.github/workflows/portal-frontend.yml` | Verwijder SENTRY_AUTH_TOKEN, update build env |

---

## 7. Objectieve Beoordeling

### Argumenten VOOR Faro migratie

1. **Minder containers**: -2 tot -4 containers, ~256 MB-1 GB RAM besparing
2. **Unified stack**: Alles in Grafana ecosystem (metrics + logs + errors)
3. **Web Vitals ingebouwd**: Faro vangt Web Vitals automatisch zonder extra lib
4. **Grotere organisatie**: Grafana Labs (100+ devs) vs GlitchTip (<1 FTE)
5. **Apache 2.0 licentie**: Faro SDK is permissief gelicentieerd

### Argumenten TEGEN Faro migratie

1. **Geen error grouping (self-hosted)**: Errors worden raw log lines. De "Issues" view, error count, first/last seen, resolve/ignore — allemaal weg. Je moet dit zelf bouwen in Grafana.
2. **Geen TanStack Router support**: Klai verliest automatische route tracking. Handmatige instrumentatie vereist.
3. **faro.receiver heeft geen metrics output**: Web Vitals gaan als logs naar VictoriaLogs, niet als Prometheus histograms. Geen histogram_quantile() voor percentile queries. Dit is objectief slechter dan het bestaande SPEC-PERF-001 plan (web-vitals → prometheus_client histograms).
4. **Source map regressie**: Alloy 1.8.0+ heeft een bekende bug met filesystem-based source maps. De fix is nog niet gereleased.
5. **Meer custom code**: TanStack Router instrumentatie, consola transport, error dashboards, alerting — allemaal custom code dat onderhouden moet worden.
6. **Error history verlies**: 30d VictoriaLogs retention vs. permanent GlitchTip archive.
7. **Complexity shift**: Je verwijdert 2-4 containers maar voegt significant meer configuratie en custom code toe. Het is niet "minder complex" — het is "anders complex".

### Future-Proof Analyse (3+ jaar)

| Factor | GlitchTip | Faro (self-hosted) |
|--------|-----------|-------------------|
| Vendor lock-in | Nee (MIT, Sentry SDK protocol) | Nee (Apache 2.0, OTel) |
| Community momentum | Stabiel maar klein team | Groot team, actieve ontwikkeling |
| Risk van abandonment | Matig (klein team, <1 FTE) | Laag (Grafana Labs backing) |
| Feature gap groei | GlitchTip blijft achter op Sentry | Self-hosted Faro blijft achter op Cloud |
| Migration path uit | Sentry SDK → elk Sentry-compatibel platform | Faro SDK → alleen Grafana ecosystem |

GlitchTip's grootste risico is het kleine team. Maar: als GlitchTip stopt, is migratie naar Faro (of Sentry, of Highlight.io) altijd nog mogelijk. De omgekeerde migratie (Faro → iets anders) is moeilijker omdat Faro een Grafana-specifiek SDK is.

---

## 8. Conclusie en Aanbeveling

### Aanbeveling: Behoud GlitchTip + implementeer bestaand SPEC-PERF-001 plan

**Waarom NIET migreren naar Faro:**

De kern van het probleem is dat **self-hosted Faro geen error tracking platform is** — het is een telemetry collection pipeline. De features die Faro competitief maken (error grouping, source map UI, error overview) zijn Grafana Cloud-only. Bij self-hosted Faro moet je zelf error grouping, dashboards, en alerting bouwen bovenop raw log queries. Dit is objectief meer werk en levert een inferieur resultaat op vergeleken met GlitchTip's built-in error management.

De "minder bewegende delen" redenering gaat niet op: je verwijdert GlitchTip's containers maar voegt custom code, configuratie, en dashboards toe die GlitchTip's functionality handmatig repliceren.

**Waarom WEL het bestaande SPEC-PERF-001 plan:**

Het huidige plan (web-vitals lib → POST /api/vitals → prometheus_client histograms → Alloy scrape → VictoriaMetrics → Grafana) is objectief beter dan Faro voor Web Vitals monitoring:

1. **Native Prometheus histograms**: histogram_quantile() voor p50/p95 — standaard, performant, betrouwbaar
2. **Volgt bestaand patroon**: Identiek aan retrieval-api /metrics scraping
3. **Geen Alloy config complexiteit**: 5 regels scrape block vs. faro.receiver + source maps + log routing
4. **~1.5 kB bundle**: web-vitals is kleiner dan Faro SDK
5. **Gescheiden concerns**: Error tracking (GlitchTip) en performance monitoring (VictoriaMetrics) zijn aparte systemen met aparte tools — elk geoptimaliseerd voor hun doel

**Bias check uitkomst:**

De bekendheid met Sentry creëert inderdaad bias richting GlitchTip. Maar het objectieve onderzoek bevestigt dat GlitchTip + web-vitals de betere keuze is voor Klai's specifieke situatie (self-hosted, klein team, EU-only). Als Klai Grafana Cloud zou gebruiken, zou Faro een sterkere kandidaat zijn.

### Mogelijke Verbeteringen aan SPEC-PERF-001

Het bestaande SPEC-PERF-001 is solide. Overwegingen voor de volgende iteratie:
1. GlitchTip all-in-one upgrade (v6) — vermindert containers van 3 naar 2
2. Monitor GlitchTip community health jaarlijks — als ontwikkeling stopt, heroverwegen
3. Als Grafana Faro self-hosted error grouping toevoegt (open source), heroverwegen

---

## Bronnen

- [GlitchTip 6 Release](https://glitchtip.com/blog/2026-02-03-glitchtip-6-released/)
- [GlitchTip Source Map Epic (#7)](https://gitlab.com/groups/glitchtip/-/epics/7)
- [GlitchTip Installation Guide](https://glitchtip.com/documentation/install/)
- [Grafana Faro Web SDK](https://github.com/grafana/faro-web-sdk)
- [Grafana Faro OSS](https://grafana.com/oss/faro/)
- [Alloy faro.receiver Docs](https://github.com/grafana/alloy/blob/main/docs/sources/reference/components/faro/faro.receiver.md)
- [Alloy Source Map Regression (#3608)](https://github.com/grafana/alloy/issues/3608)
- [VictoriaLogs Loki Push API](https://docs.victoriametrics.com/victorialogs/data-ingestion/)
- [GlitchTip vs Sentry Comparison](https://earezki.com/ai-news/2026-03-14-glitchtip-vs-sentry/)
- [Grafana Private Source Map Uploads GA](https://grafana.com/whats-new/2025-12-11-private-source-map-uploads-now-ga/)
