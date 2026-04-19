# Acceptance Criteria — SPEC-SEC-009

EARS-format acceptance criteria for the SERVERS.md documentation-drift fix. These criteria are satisfied entirely by markdown edits; no code, config, or runtime behavior is asserted.

---

## AC-1: Completeness of the public route table

- **WHEN** a reviewer reads the Caddy state section of `klai-infra/SERVERS.md` **THE** section **SHALL** list every route present in `/opt/klai/caddy/Caddyfile`.
- **WHEN** `/opt/klai/caddy/Caddyfile` is modified (add, remove, or change a public route) **THE** SERVERS.md update **MUST** land in the same PR. A PR that touches `Caddyfile` without a matching SERVERS.md diff fails review.
- **WHERE** a route's auth layer or rate limit is unknown **THE** table **SHALL** contain the literal string `UNKNOWN` plus a pointer to the SPEC that will resolve it (e.g. "see SEC-008"), not an omission or blank cell.

## AC-2: Findings F-017 through F-022 addressed

- **WHEN** a reviewer searches SERVERS.md for `connector.getklai.com` **THE** file **SHALL** return the row: route `connector.getklai.com`, target `klai-connector:8200`, auth `Zitadel introspection + portal bypass (SEC-004 scope)`, rate-limit `60/min/IP`, comment `F-017 PUBLIC — contradicted old SERVERS.md text`.
- **WHEN** a reviewer searches SERVERS.md for `dev.getklai.com` **THE** file **SHALL** return a row for both the API route and the SPA fallback, each marked public, with auth `Zitadel (audit pending SEC-008)` and a comment `F-018 dev env publicly reachable`.
- **WHEN** a reviewer searches SERVERS.md for `/kb-images/*` **THE** file **SHALL** return a row target `garage:3902`, auth `anonymous reads (by design — browser image access)`, comment `F-019 intentional`.
- **WHEN** a reviewer searches SERVERS.md for `/bots/*` **THE** file **SHALL** return target `vexa-bot-manager:8000`, auth `UNKNOWN (see SEC-008)`, rate-limit `10/min/IP`, comment `F-020 pending audit`.
- **WHEN** a reviewer searches SERVERS.md for `logs-ingest.getklai.com` **THE** file **SHALL** return target `victorialogs:9428`, auth `token-gated (single secret)`, comment `F-021`.
- **WHEN** a reviewer searches SERVERS.md for `/docs/*` **THE** file **SHALL** return target `docs-app:3010`, auth `UNKNOWN (see SEC-008)`, rate-limit `60/min/IP`, comment `F-022 pending audit`.

## AC-3: Internal-vs-public boundary is explicit

- **WHEN** a reviewer reads the Caddy state section **THE** section **SHALL** contain a subsection titled `Docker-internal only (NOT exposed via Caddy)` that names at minimum: retrieval-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp, PostgreSQL, Qdrant, FalkorDB, Ollama.
- **WHILE** the internal-only subsection exists **THE** subsection **SHALL** cross-reference the "Docker networks" subsection already present in SERVERS.md, so network-membership claims can be verified on the same page.
- **WHEN** a reviewer wants to answer "is service X publicly reachable?" **THE** reviewer **SHALL** find a definitive yes/no in the Caddy state section (either a row in the public route table, or a line in the internal-only subsection) without needing shell access to core-01.

## AC-4: Maintenance warning is present and actionable

- **WHEN** a contributor opens the Caddy state section **THE** top of the section **SHALL** contain a warning block: "Keep this section in sync with `/opt/klai/caddy/Caddyfile` — mismatched public routes are a security risk. Last verified against live Caddyfile on 2026-04-19; see `.moai/audit/04-3-prework-caddy.md` for the verified snapshot."
- **WHEN** a contributor changes the Caddyfile without updating SERVERS.md **THE** PR review **SHALL** block on the missing update (enforced by reviewer per the klai workflow; optional CI guard out of scope per plan.md step 4).

## AC-5: Single source of truth format

- **WHEN** both SERVERS.md and `.claude/rules/klai/infra/servers.md` (or `.claude/rules/klai/platform/caddy.md`) exist **THE** Caddy route table **SHALL** either be identical in both files OR the rules file **SHALL** contain only a pointer to SERVERS.md for the full route list (no partial mirrors).
- **WHILE** the table format is markdown **THE** column order **SHALL** be: `Route | Target | Auth layer | Rate limit | Internal/Public | Comment` — maintained identically wherever the table is mirrored.

## AC-6: No functional changes

- **WHILE** this SPEC is implemented **THE** PR **SHALL NOT** modify any file other than `klai-infra/SERVERS.md`, `.claude/rules/klai/infra/servers.md`, `.claude/rules/klai/platform/caddy.md` (if present), and the SPEC artifacts themselves.
- **WHEN** CI runs on the PR **THE** only affected checks **SHALL** be markdown-lint and link-check (if configured); no service, container, Caddy, or SOPS check **SHALL** be triggered by this PR's diff.
