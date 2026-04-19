# Implementation Plan — SPEC-SEC-009

Documentation-only plan. No code, no migrations, no service restarts. All edits are markdown.

---

## Step 1 — Rewrite the Caddy state section in `klai-infra/SERVERS.md`

Target: the "Caddy state (2026-03-12)" block at roughly lines 89 through 106 of `klai-infra/SERVERS.md`.

### 1.a — Replace the bulleted route list with a full table

Current format is a short bulleted list under `Routes:` that covers roughly 10 routes. Replace it with the complete 15-row table from `.moai/audit/04-3-prework-caddy.md` § "Caddy verify — FINDINGS" (the table between "Complete lijst uit live `/opt/klai/caddy/Caddyfile`:" and "**NIET publiek:**").

Column order for the new table:

| Route | Target | Auth layer | Rate limit | Internal/Public | Comment |
|---|---|---|---|---|---|

All 15 routes from the audit table are copied in. The `Internal/Public` column value is `Public` for every row in this table (they are, by definition, exposed through Caddy). The column exists so the internal-only subsection (step 1.c) can share the same schema.

### 1.b — Add the maintenance warning block

Directly above the table, add a warning callout:

> ⚠ Keep this section in sync with `/opt/klai/caddy/Caddyfile` on every Caddy change — mismatched public routes are a security risk. Last verified against the live Caddyfile on 2026-04-19; diff against `.moai/audit/04-3-prework-caddy.md` § "Caddy verify — FINDINGS" when updating.

(Emoji is acceptable in SERVERS.md per existing style; if the file is emoji-free, use plain `WARNING:` prefix.)

### 1.c — Add the "Docker-internal only" subsection

Immediately below the public route table, add:

> ### Docker-internal only (NOT exposed via Caddy)
>
> The following services are reachable only from other containers on their respective Docker networks. They have no Caddy route and no public DNS record:
>
> - `retrieval-api` — on `klai-net-postgres`, `klai-net` (internal callers: portal-api, research-api, LiteLLM hook)
> - `knowledge-ingest` — on `klai-net`, `klai-net-postgres` (internal callers: portal-api, scribe-api, crawler)
> - `klai-mailer` — on `klai-net` (internal caller: portal-api)
> - `klai-knowledge-mcp` — on `klai-net`, `klai-net-postgres` (internal caller: LibreChat MCP client)
> - `postgresql` — on `klai-net-postgres` (see Docker networks subsection for membership)
> - `qdrant` — on `klai-net` (internal caller: retrieval-api)
> - `falkordb` — on `klai-net` (internal caller: retrieval-api via Graphiti)
> - `ollama` — on `klai-inference` (internal caller: LiteLLM only; no outbound internet)
> - LibreChat per-tenant containers that do NOT have a `chat-{slug}` Caddy route
>
> See the "Docker networks" subsection below for full network membership. These boundaries are defense-in-depth: even if Zitadel introspection at a public route is bypassed, these services remain unreachable from the internet.

### 1.d — Update the existing preamble line

The existing "Caddy state (2026-03-12)" heading has a stale date. Change to:

> **Caddy state (verified 2026-04-19):**

Keep the existing bullets for wildcard cert, custom image, routing convention, security headers, Zitadel reverse_proxy, and admin interface. Only the `Routes:` bullet is replaced by the table from step 1.a.

## Step 2 — Cross-reference the audit

At the bottom of the Caddy state section, add:

> **Audit reference:** `.moai/audit/04-3-prework-caddy.md` contains the 2026-04-19 live-Caddyfile snapshot used to verify this table. When the Caddyfile changes, update this table and add a row to the "Changelog" at the bottom of SERVERS.md; re-verify on the next security audit by comparing live `/opt/klai/caddy/Caddyfile` against this section.

## Step 3 — Mirror the table into the Klai rules files

Check whether `.claude/rules/klai/platform/caddy.md` exists.

- **If it exists and contains a route list:** replace that list with the same table from step 1.a, keeping the column schema identical. Add a top-of-file pointer: "Source of truth: `klai-infra/SERVERS.md` § Caddy state." Delete any content that duplicates SERVERS.md beyond the route table.
- **If it does not exist:** create nothing. A rules file is not required; SERVERS.md is authoritative.

Also check `.claude/rules/klai/infra/servers.md`. It currently focuses on SSH, IPs, firewall rules, and DNS — not Caddy routes. Leave that file unchanged unless it has gained a Caddy-routes subsection since 2026-04-19; if so, replace with the same table.

Do not introduce any new rules file for this SPEC.

## Step 4 — Future automation hint (optional, NOT in scope)

Add a one-line note at the end of SERVERS.md's "Open items" or changelog:

> - [ ] (future) Add a pre-commit or CI guard that fails when `deploy/caddy/` or `core-01/caddy/` changes without a matching diff in `SERVERS.md` § Caddy state.

This is an optional backlog item, not a deliverable of this SPEC. It gives a future contributor a concrete ticket to pick up without forcing SEC-009 to ship a hook.

## Step 5 — Verify and PR

1. `git diff --stat` on `klai-infra/` — must show `SERVERS.md` changed, plus optionally `.claude/rules/klai/platform/caddy.md`. No other paths.
2. Markdown preview locally — the new table renders, the warning block stands out, the internal-only subsection is clearly separated.
3. Open a PR titled `docs(infra): sync SERVERS.md Caddy routes with live Caddyfile (SEC-009)`. Body lists F-017 through F-022 as addressed and links `.moai/audit/04-3-prework-caddy.md`.
4. Reviewer checks: does every row in the audit table appear? Are F-020 and F-022 marked `UNKNOWN (see SEC-008)`? Is the internal-only list complete? If yes on all three, merge.

No deploy step. No service restart. Merging to `main` is the deliverable.

---

## Files touched (summary)

| File | Change |
|---|---|
| `klai-infra/SERVERS.md` | Rewrite Caddy state section: new table, warning block, internal-only subsection, audit cross-reference, updated heading date |
| `.claude/rules/klai/platform/caddy.md` | If exists and contains a route list: replace with the same table + pointer to SERVERS.md. Otherwise untouched |
| `.claude/rules/klai/infra/servers.md` | Untouched unless it has gained a Caddy-routes subsection (it did not as of 2026-04-19) |

## Non-goals

- No Caddyfile edits
- No auth additions or hardening (that is SEC-008)
- No CI / pre-commit implementation (step 4 is a backlog note only)
- No changes to Docker networks subsection, disaster-recovery procedure, Zitadel subsection, or any other part of SERVERS.md
