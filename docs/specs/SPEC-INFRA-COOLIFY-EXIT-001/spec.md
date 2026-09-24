---
id: SPEC-INFRA-COOLIFY-EXIT-001
version: "0.1.0"
status: draft
created: 2026-09-24
updated: 2026-09-24
author: Mark Vletter
priority: high
related:
  - SPEC-INFRA-AI-WORKFLOW-001
  - SPEC-INFRA-CONFIG-SYNC-001
  - SPEC-CI-TRIVY-POLICY-001
---

# Move the Coolify-managed services into the deployment pipeline

## 1. Problem and production evidence

public-01 runs eighteen containers. Six of them are Coolify itself — the app,
its Postgres 15, its Redis, its Traefik proxy, its realtime service and its
sentinel. A third of the machine runs the thing that runs the things.

What Coolify manages is four services and one application:

| resource | kind | containers |
|---|---|---|
| Twenty CRM | service | `twenty`, `worker`, `postgres:16-alpine`, `redis:7-alpine` |
| Umami | service | `umami`, `postgres:16-alpine` |
| Fider | service | `fider`, `postgres:12` |
| Uptime Kuma | service | `uptime-kuma` |
| getklai.com website | application | one image tagged with the deployed commit SHA |

`klai-alloy-public01` carries no Coolify labels and is ours already.

Every one of the four services is behind, and two of them cannot even say by
how much, because they are pinned to a tag that moves:

| service | pin | actually running | latest | gap |
|---|---|---|---|---|
| Twenty | `v1.20.0` | v1.20.0 | v2.42.2 | a full major |
| Uptime Kuma | `2` | 2.1.3 | 2.5.5 | four minors |
| Fider | `stable` | image built 3 March | v0.37.0 | about seven months |
| Umami | `3.0.3` | 3.0.3 | v3.4.0 | four minors |
| Fider database | `postgres:12` | 12.22 | — | 12.22 is the final PostgreSQL 12 release; the series left support in November 2024 |

Reading that table required `docker exec` into two containers, because the tag
does not answer the question. Uptime Kuma is the system that tells us
production is broken, and it is four minors behind on a moving tag.

The second failure is worse than staleness, because staleness at least sits
still. Coolify keeps the compose definition in its own database and regenerates
the deployed file from it. For Twenty that stored copy says `v1.15` while the
host runs `v1.20.0`: pressing redeploy today rolls the CRM back five versions.
Nothing could have seen that, because the authoritative copy is a column in a
database rather than a file in git.

None of this happens to the fifty services on core-01. There the pin is in
git, Renovate opens a PR when a version appears, CI refuses a tag that is not
pullable or not pinned, the env-scope guard and the volume audit run on every
change, the deploy verifies that recreated containers are actually running, and
Trivy scans weekly. The difference between the two halves of our estate is not
care. It is whether the declaration lives in a file.

### The AI-first argument

On 23 and 24 September the same class of work was attempted on both halves.

On core-01, decommissioning the self-hosted cal.com ran end to end without a
human in the loop: research, an export verified by restoring it, removal of the
service, the route, the registry entry and the invite default in one reviewed
PR, a deploy that proved itself, a fall-through bug found in production and
fixed, and a domain move. Every step left a diff and a green check.

On Coolify, the same work stopped at the backup. Not because the task was
harder, but because the control plane is a GUI whose state lives in its own
Postgres, reachable only with a credential in a password manager. An agent can
read, diff, review and verify a file. It can do none of those to a database
column behind a login.

That is the value question answered empirically. For the four services Coolify
contributes nothing that compose and Caddy do not already do fifty times over.
For the website it does real work — watch the repo, build on push, tag the
image with the commit SHA — but that is a build pipeline, and Klai already runs
one for ten of its own images.

## 2. Contract

1. Every service currently managed by Coolify shall have its version declared
   in a file in git, and that declaration shall be what the deploy applies.
2. Migration and upgrade shall be separate changes, in that order. A service
   moves at the version it is already running, so that the move is provably a
   relocation and nothing else; the upgrade that follows happens inside the
   pipeline, as a reviewable diff with the usual guards.
3. No service shall be pinned to a moving tag after its migration.
4. A migration shall be reversible by DNS until its old containers are removed.
   `crm`, `analytics`, `feedback` and `status` each hold an explicit A record
   to public-01 that overrides the `*.getklai.com` wildcard, so the cutover is
   a record change and the rollback is the same record change back.
5. Old containers, volumes and backup files shall be removed only after the
   migrated service has been verified in production, and for the CRM only after
   the historical import has also been verified.
6. Uptime Kuma shall not run on core-01. Monitoring does not belong on the
   machine it monitors. It leaves Coolify without moving host.

## 3. Order, and why the CRM goes first

The CRM goes first even though it is the largest, because it is the one with
work already queued behind it: 52 people and 64 meetings from the
decommissioned cal.com are waiting to be imported, and the Twenty upgrade is a
full major. Doing it first means the template is proven on the hardest case
rather than the easiest, and it unblocks the cal.com cleanup that is currently
holding a database and two backup sets open.

The remaining order is by blast radius: Umami (analytics, no customer data),
Fider (public feedback), Uptime Kuma (monitoring, stays off core-01), and the
website last, since it is the only one where Coolify does work worth replacing.

## 4. Twenty CRM

### T1 — Relocate at the current version

Twenty moves to core-01 still on `v1.20.0`, with its own `postgres:16-alpine`
and `redis:7-alpine`, matching what runs today. Keeping the engine version
identical is what makes this step a relocation: if something breaks, it is the
move and not a database upgrade.

The change adds a compose service, a `crm.{$DOMAIN}` Caddy site, the `CRM_*`
secrets in SOPS, a `volume-mounts.yaml` entry for the Postgres volume, and a
`platform_subdomains.py` entry. That is the same shape as the Calnode addition
of 23 September, which landed as one PR.

The data moves by dump and restore. The verified dump at
`/root/backups/twenty-preupgrade-20260924-0940/twenty-db.dump` on public-01
restores to one workspace schema and two users; a fresh dump is taken at
cutover and the same restore check repeated on core-01 before the A record
moves.

### T2 — Upgrade inside the pipeline

Twenty ships a per-version upgrade command and a cross-upgrade window. The
window is `TWENTY_PREVIOUS_VERSIONS`, 46 entries beginning at `1.21.0`, and a
pin below that floor is rejected with an explicit instruction to reach the
floor first. We are on `1.20.0`, exactly one step below it.

So the upgrade is two stages, not forty-six:

1. `v1.21.0`, then its upgrade command. This lifts the workspace into the
   supported window.
2. `v2.42.2`, then `upgrade --dry-run`, read what it intends, then for real.
   That command walks the remaining steps itself.

PostgreSQL 16 and Redis 7 stay: the upstream compose for the current version
still specifies `postgres:16` and `redis`. No engine migration is part of this.

Each stage is its own PR with its own deploy and its own verification, because
a failed schema migration is the one thing here that a DNS flip cannot undo.

### T3 — Import the historical appointments

Only after T2 is verified. The import needs a Twenty API key; the one in SOPS
is rejected by the server as revoked, and minting a replacement is a decision
for the owner of the CRM rather than for whoever is running the migration.

The script exists and is dry-run clean against the export: 52 people, 64 notes,
10 of those people known only by an email address. It is idempotent by email
and by note title, so a partial run is repeated rather than unpicked, and it
carries a `--verify` mode that reads everything back out of Twenty and compares
it against the export, naming anything missing.

Order of operations: one person as a canary, inspect that record by hand,
then the remaining 51, then `--verify` until it is clean.

### T4 — Remove what it replaced

Only after T3 verifies clean:

- the Coolify Twenty service and its four containers;
- the `cal-db` database on core-01, the `cal_user` role and the three `CAL_*`
  secrets, which are the last live remnant of the decommissioned booking tool;
- the cal.com export sets, once the same data is demonstrably in the CRM;
- the pre-upgrade Twenty dumps, once the upgraded instance has served a week.

## 5. Umami, Fider, Uptime Kuma

Each follows T1 then T2, with no T3 or T4 beyond removing its Coolify service.

**Umami** is the rehearsal that costs least: analytics, no customer data, one
container and one Postgres 16. Migrate at `3.0.3`, then upgrade to `v3.4.0`.

**Fider** carries two problems, and they are separable. The application moves
off the moving `stable` tag onto whatever digest it is actually running today,
and only afterwards is upgraded to `v0.37.0`. Its database is the exception to
"relocate at the current version": `postgres:12` has been out of support since
November 2024 and `12.22` is the last release it will ever receive, so the
migration is also the moment it leaves PostgreSQL 12. That is a dump-and-restore
across major versions and it gets its own verification.

**Uptime Kuma** stays on public-01 and still leaves Coolify. It needs the one
genuine extension of the pipeline in this SPEC: the compose sync that
`deploy-compose.yml` performs against core-01, pointed at a second host. Its
push tokens are already referenced by `push-health.sh`, so the monitors and
their tokens survive the move unchanged.

## 6. The website, and then Coolify itself

The website is the only resource where Coolify earns its place: it watches the
repository, builds on push, and tags the image with the deployed commit SHA.
Replacing it means a GitHub Actions build pushing to GHCR and a deploy from
there — the pattern Klai already runs for ten images. It is deliberately last,
and it is the one decision in this SPEC that could reasonably go the other way.

When all five have moved, Coolify's own six containers go with them, and with
them a beta-versioned control plane, a second Postgres, a second Redis, and a
second reverse proxy running beside Caddy with its own TLS and routing.

## 7. Capacity

core-01 uses 26 GB of 62, carries a load average of 1.36 across 20 cores, and
has 188 GB of 436 in use across 91 containers. The three services that move
there are small: the Twenty database is 20 MB. Capacity is not a constraint on
this plan, and should not be presented as one.

## 8. Acceptance criteria

1. No image running on public-01 or core-01 is pinned to a moving tag.
2. For every migrated service, the version in git, the version Coolify would
   deploy (while it still exists), and the version running are the same, and a
   check proves it rather than a person asserting it.
3. Twenty serves `crm.getklai.com` from core-01 on `v2.42.2`, and its workspace
   contains the 52 people and 64 notes from the cal.com export, confirmed by
   `--verify` reporting nothing missing.
4. `cal-db`, the `cal_user` role and the `CAL_*` secrets no longer exist.
5. The weekly `twenty-version-check` passes, including its host half.
6. Uptime Kuma runs from a git-declared compose file on public-01 and its
   existing monitors and push tokens are unchanged.
7. Coolify is removed from public-01, or a written decision records why the
   website justifies keeping it.

## 9. Verification

Each migration is verified the way the cal.com decommission was, because that
is the standard this SPEC exists to extend to the other half of the estate: a
dump restored into a throwaway database and its row counts compared against the
source before the cutover; the deploy job proving the recreated containers are
running; the public URL answering over TLS after the DNS change; and the
service's own health endpoint checked on the host rather than inferred.

The Twenty upgrade additionally runs `upgrade --dry-run` and its output is read
before the real run, and the import is verified by `--verify` rather than by
the absence of errors during the run.

## 10. Deliberately not included

- Moving Uptime Kuma to core-01. It monitors that host.
- Upgrading PostgreSQL for Twenty or Umami. Both sit on 16, which is what
  upstream specifies; changing it during a relocation would confound the move.
- Replacing Traefik with Caddy on public-01 as a separate step. Traefik leaves
  when Coolify leaves; introducing a third state in between buys nothing.
- Renovate on klai-infra. The weekly version check covers the same gap for the
  services that stay off core-01, and adding a second Renovate installation is
  a larger decision than this SPEC needs to make.
