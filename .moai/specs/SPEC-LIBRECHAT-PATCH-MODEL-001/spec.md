---
id: SPEC-LIBRECHAT-PATCH-MODEL-001
version: "0.1.0"
status: draft
created: "2026-08-13"
updated: "2026-08-13"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-08-13 | MoAI | Initial draft. Triggered by the 0.8.6→0.8.7 LibreChat upgrade taking three attempts on 2026-08-13 (`307643ad7` revert, `6206ac51b` revert, `5e041bcb2` landed) plus an adversarial review that found the SHA manifest proves upstream provenance but not patched-file provenance, and that full-file byte snapshots break on every bundler change even when nothing semantic changed. Proposes replacing the five compiled/full-file patches under `deploy/librechat/patches/` with minimal source-level diffs built via upstream's own toolchain into a Klai-owned image. |

# SPEC-LIBRECHAT-PATCH-MODEL-001: Source-level LibreChat patch model

## Overview

Klai runs LibreChat as its chat frontend/backend and modifies it in three ways today, none of which are minimal or reproducible from source:

1. **Five full-file replacements** under `deploy/librechat/patches/` (and the byte-identical `getklai/patches/` canary mirror), bind-mounted over files inside the stock `ghcr.io/danny-avila/librechat:v0.8.7` image at container start. Three of these are **compiled output** of `@librechat/agents` (a separate upstream repo, `danny-avila/agents`, bundled via rolldown into `dist/cjs/*.cjs`); two are **LibreChat app source** files (already unbundled JS/TS, but still snapshotted whole). `deploy/librechat/patch-manifest.txt` pins a SHA256 of each file's *upstream original* and `deploy/librechat/check-patch-drift.sh` fails CI if that hash no longer matches the pinned image.
2. **Runtime string-transforms** applied by `deploy/librechat/klai-entrypoint.sh` (and its near-duplicate `getklai/entrypoint.sh`) at container boot: a Node.js regex rewrite of the Meilisearch index names baked into `@librechat/data-schemas`' compiled output (tenant isolation — `SEARCH=true` without it would share one global Meili index across every tenant), and an additive `<head>` HTML injection into the built SPA `index.html` (force-light theme, hide LibreChat's footer, wrap KB source/activity blocks in a collapsible disclosure).
3. **A byte-identical `getklai/` canary copy** of nearly all of the above, kept in lockstep by hand and by `deploy/librechat/tests/*.test.cjs` sync-guard tests.

None of this is expressed as a diff against a known upstream commit. Each of the five patches is a full snapshot of a file Klai does not own; upgrading LibreChat means re-extracting each target file fresh from the new image and manually re-applying Klai's changes by inspection, with `patch-manifest.txt`'s SHA the only mechanical signal that the *starting point* moved.

### What this cost on 2026-08-13

The 0.8.6→0.8.7 bump took three attempts:

- **Attempt 1** (`80a95da5e`, reverted by `307643ad7`): blocked outright — `@librechat/agents` switched its bundler between the two LibreChat versions, so `dist/cjs/*.cjs` was re-emitted with a different byte layout. `check-patch-drift.sh`'s SHA check correctly refused to proceed, but gave no path forward beyond "go re-diff by hand."
- **Attempt 2** (`efd07680e`, reverted by `65caf3586`, root-caused in `e0e90cc77`'s revert): patches were rebased onto the new bundle (`1d6131070`, `5cd077b3d`) and passed the manifest check, but the **runtime Meili entrypoint transform** was never covered by that check — `@librechat/data-schemas` had also gone from three per-model files (`dist/models/message.cjs`, `convo.cjs`, `plugins/mongoMeili.cjs`) to one bundled `dist/index.cjs`, the entrypoint's hardcoded per-model paths no longer existed, and the getklai canary crashlooped in production.
- **Attempt 3** (`5e041bcb2`, landed): shipped a version-aware Meili transform that detects and handles both the pre-rolldown and bundled `data-schemas` shapes, plus `check-patch-drift.sh`'s `validate_runtime_targets()` — a preflight that at least confirms the runtime patch *targets* still exist in the pinned image before deploy.

A follow-up adversarial review (2026-08-13) found two structural gaps that attempt 3 does not close:

- **Patched-file provenance is unverifiable.** `patch-manifest.txt` proves the *upstream original* matches a known hash before Klai's patch is applied. It says nothing about whether the *patched file actually deployed* is what review approved — an incorrectly-based or hand-edited patch still passes CI as long as the upstream original's hash is unchanged.
- **`validate_runtime_targets()` only checks file existence, not that the transform still applies.** Upstream can keep a file's path stable while reshaping its internal syntax (renamed variable, reshaped function, moved anchor), which passes this preflight and then crashloops (Meili, fail-loud) or silently drops behaviour (feedback forwarding, which historically failed open) at container boot. A dry-run-transform preflight closing this specific gap is in flight on `fix/librechat-preflight-feedback-config` (not yet merged into `main` as of this SPEC) — see [Relationship to in-flight preflight work](#relationship-to-in-flight-preflight-work).

Both gaps are symptoms of the same root cause: Klai does not maintain a diff against a known upstream source. It maintains full-file snapshots and only checks a hash of where they *started*.

### Target model

Replace the five full-file patches with **minimal source-level diffs** maintained against the actual upstream TypeScript/JavaScript source trees (`danny-avila/agents` and `danny-avila/LibreChat`), applied in CI, built with upstream's own toolchain, and assembled into a Klai-owned image (`ghcr.io/getklai/librechat:<upstream-tag>-klai.<n>`) that CI both builds and can prove was built from exactly those diffs against exactly that upstream tag. Runtime entrypoint transforms migrate into the same model where the transform's target is a static file with no per-tenant runtime dependency; transforms that genuinely need a runtime-only value (the Meili tenant index) or that are runtime-safe by design (the additive client-polish injection) stay as runtime transforms, explicitly justified per surface rather than by default.

## Current-state inventory

All eight current Klai modification surfaces to LibreChat, their present mechanism, and their target home under this SPEC.

| # | Surface | Current mechanism | Current file(s) | Upstream repo + path | Target under new model |
|---|---------|-------------------|------------------|------------------------|--------------------------|
| 1 | Media/message formatting guards | Full compiled-file bind-mount | `deploy/librechat/patches/format.cjs` (+ `getklai/patches/`) | `danny-avila/agents` — `src/messages/format.ts` (rolldown-bundled to `dist/cjs/messages/format.cjs`) | **MIGRATE** — source diff, built via agents' own toolchain (Lane A, see [Build pipeline](#build-pipeline-design)) |
| 2 | Source-streaming / sparse-aggregation guards | Full compiled-file bind-mount | `deploy/librechat/patches/stream.cjs` | `danny-avila/agents` — `src/stream.ts` | **MIGRATE** — Lane A |
| 3 | Web-search behavior | Full compiled-file bind-mount | `deploy/librechat/patches/search.cjs` | `danny-avila/agents` — `src/tools/search/search.ts` | **MIGRATE** — Lane A |
| 4 | Public-share sanitization | Full source-file bind-mount (already unbundled JS) | `deploy/librechat/patches/share.js` | `danny-avila/LibreChat` — `api/server/routes/share.js` | **MIGRATE** — source diff, no build step, direct `COPY` (Lane B) |
| 5 | Completed-generation-job retention | Full source-file bind-mount (TS source, LibreChat internal workspace package) | `deploy/librechat/patches/createStreamServices.ts` | `danny-avila/LibreChat` — `packages/api/src/stream/createStreamServices.ts` | **MIGRATE** — source diff, built via LibreChat's own `packages/api` build step (Lane B; exact build invocation is a Phase 1 open question) |
| 6 | KB-feedback forwarding to portal-api | **Currently dead**: orphaned `deploy/librechat/entrypoint.sh` (SPEC-KB-015 carrier, referenced by no deployment path) plus a `feedback.cjs`/`feedback.patch` pair also unreferenced. A runtime re-wire is in flight on `fix/librechat-preflight-feedback-config` (unmerged). | `deploy/librechat/entrypoint.sh`, `patches/feedback.cjs`, `patches/feedback.patch` | `danny-avila/LibreChat` — `api/server/routes/messages.js` | **MIGRATE** — source diff (Lane B); supersedes both the dead full-file patch and the in-flight runtime-transform. See [open question](#open-questions) on sequencing against the unmerged branch. |
| 7 | Tenant-scoped Meilisearch index names | Runtime Node regex rewrite against compiled `@librechat/data-schemas` output, dual-shape (legacy per-model files vs. rolldown-bundled `dist/index.cjs`) | `deploy/librechat/klai-entrypoint.sh`, `getklai/entrypoint.sh` | `danny-avila/LibreChat` (via `@librechat/data-schemas` dependency) — compiled, no stable TS source anchor exposed | **KEEP RUNTIME** — `MEILI_MESSAGES_INDEX`/`MEILI_CONVOS_INDEX` are per-tenant values not knowable at image-build time; one shared image serves every tenant. Stays a fail-loud runtime transform. |
| 8 | Client polish (force-light theme, hide footer, KB source/activity disclosure) | Runtime additive `<head>` HTML injection into built SPA `index.html` | `deploy/librechat/klai-entrypoint.sh`, `getklai/entrypoint.sh` | `danny-avila/LibreChat` client (Vite-built SPA; `client/dist/index.html` is a build artifact, not source) | **KEEP RUNTIME** — additive-only by design (never rewrites the hashed `/assets/index-<hash>.js` reference), which is precisely why it already survives upgrades automatically today. Migrating to source-level would require rebuilding the full Vite client bundle in CI for a purely cosmetic feature, adding cost and a new failure surface without a correctness benefit the runtime version doesn't already have. |

Six of eight surfaces migrate to source-level diffs (rows 1–6). Two stay runtime, each for a distinct, explicit reason (row 7: genuinely per-tenant value; row 8: additive injection already decoupled from compiled internals by design) rather than by default inertia.

## Build pipeline design

Two build lanes feed one final image assembly stage.

**Lane A — `@librechat/agents` package rebuild** (surfaces 1–3):
1. Resolve the exact `@librechat/agents` version/commit that the pinned `danny-avila/LibreChat` tag depends on (read from that tag's lockfile — never an independently floating pin, to avoid the version-skew class of bug that caused the 2026-08-13 bundler-switch incident).
2. `git clone` `danny-avila/agents` at that resolved ref.
3. `git apply --3way` Klai's diffs for `src/messages/format.ts`, `src/stream.ts`, `src/tools/search/search.ts`. Fail the build loudly (non-zero exit, explicit hunk-failure message) on any diff that does not apply cleanly — never partial-apply, never silently skip a hunk.
4. Run the agents repo's own build script (exact invocation TBD — Phase 1 spike; currently believed to be a rolldown-driven `npm`/`pnpm` script) to produce `dist/cjs/messages/format.cjs`, `dist/cjs/stream.cjs`, `dist/cjs/tools/search/search.cjs`. CI verifies the build script it invokes still exists before invoking it (fails loudly with a remediation pointer if the agents repo restructures its build tooling again).

**Lane B — LibreChat app-source overlay** (surfaces 4–6):
1. `git clone` `danny-avila/LibreChat` at the pinned tag (the same tag already pinned in `deploy/librechat/patch-manifest.txt`, `deploy/docker-compose.yml`, and `klai-portal/backend/app/core/config.py::librechat_image`).
2. `git apply --3way` Klai's diffs for `api/server/routes/share.js`, `packages/api/src/stream/createStreamServices.ts`, `api/server/routes/messages.js`. Same fail-loud rule as Lane A.
3. `share.js` and `messages.js` are already-unbundled runtime JS — no build step, the patched file is used directly.
4. `createStreamServices.ts` lives in a LibreChat workspace package (`packages/api`) that may or may not need its own compile step to produce the artifact the running container actually loads — **Phase 1 open question**, resolved by inspecting the pinned tag's own `Dockerfile`/build scripts rather than assumed.

**Final assembly:**
```
FROM ghcr.io/danny-avila/librechat:<pinned-tag>
COPY --from=agents-build /dist/cjs/messages/format.cjs      /app/node_modules/@librechat/agents/dist/cjs/messages/format.cjs
COPY --from=agents-build /dist/cjs/stream.cjs                /app/node_modules/@librechat/agents/dist/cjs/stream.cjs
COPY --from=agents-build /dist/cjs/tools/search/search.cjs   /app/node_modules/@librechat/agents/dist/cjs/tools/search/search.cjs
COPY --from=librechat-build /api/server/routes/share.js      /app/api/server/routes/share.js
COPY --from=librechat-build /api/server/routes/messages.js   /app/api/server/routes/messages.js
COPY --from=librechat-build /packages/api/<built-artifact>   /app/packages/api/<built-artifact>
```
Tagged and pushed as `ghcr.io/getklai/librechat:<upstream-tag>-klai.<n>` (the `<n>` suffix increments per Klai-side patch revision against the same upstream tag, mirroring the existing `-local-YYMMDD-HHMM` convention used for `deploy/crawl4ai/Dockerfile` — a smaller-scale precedent for "rebuild a Klai variant FROM an upstream base image" already in the codebase, though that pattern patches installed Python packages post-`pip install` rather than rebuilding from diffed TS/JS source).

A new CI workflow (`.github/workflows/librechat-image-build.yml`, modeled on the existing `docker/build-push-action` pattern in `.github/workflows/portal-api.yml`) runs both lanes, assembles the image, and — critically — **records a build manifest**: for each patched artifact, its upstream path, the diff file that produced it, and the resulting SHA256 inside the built image. This manifest is the direct replacement for today's `patch-manifest.txt`, and closes the patched-file-provenance gap: `check-patch-drift.sh`'s successor compares the SHA256 of each patched file **inside the deployed image** against the SHA256 **CI recorded when it built that exact image tag** — not just against a hash of the upstream original.

### Upgrade day: before vs. after

**Before (2026-08-13 model), per attempt:**
1. Bump the pinned tag in three places (`patch-manifest.txt`, `docker-compose.yml`, `config.py`).
2. `check-patch-drift.sh` fails on SHA mismatch (upstream file changed shape) — expected, but gives no next step beyond manual inspection.
3. Manually `docker run --entrypoint cat` the new image, diff by eye against the old patched file, hand-craft a new full-file snapshot preserving Klai's changes.
4. Re-run drift check; if it passes, deploy to canary.
5. Runtime entrypoint transforms are a **separate, uncovered** class of patch — no mechanical signal when their targets move (attempt 2's crashloop). Discovered only by canary crashlooping in production.
6. Fix, repeat. Three full cycles before landing.

**After (this SPEC's model):**
1. Bump the pinned upstream tag in the same three places (unchanged — this remains a deliberate, reviewed action, not automatic).
2. CI runs both build lanes. `git apply --3way` either succeeds (diff still applies — most releases) or fails loudly with the exact rejected hunk, pointing directly at the changed upstream line, not requiring a full re-diff-by-eye.
3. On successful apply, CI builds the image, runs the full `deploy/librechat/tests/*.test.cjs` suite plus an integration smoke test against the freshly-built image (not just fixtures), and publishes the build manifest.
4. The runtime-transform preflight (Meili, client-polish) dry-runs against the **CI-built image**, not just the raw upstream pull — closing the "target exists but transform no longer applies" gap for the two surfaces that stay runtime.
5. Deploy to canary; `check-patch-drift.sh`'s successor verifies deployed-image SHA matches the CI build manifest before promoting past canary.
6. If the diff fails to apply, the failure is scoped to exactly the affected file and hunk — fix that diff, not the whole patch-and-inspect cycle.

## Requirements (EARS format)

### REQ-1 — Source-level diffs replace full-file snapshots
The system SHALL maintain Klai's modifications to the six MIGRATE surfaces (inventory rows 1–6) as minimal diffs against pinned commits/tags of `danny-avila/agents` and `danny-avila/LibreChat`, stored under version control, instead of full-file snapshots. `deploy/librechat/patches/*.cjs` and `patches/share.js`/`patches/createStreamServices.ts` (and their `getklai/` mirrors) SHALL be removed once the corresponding diff is verified equivalent and the new build pipeline is live for that surface.

### REQ-2 — Fail-loud diff application
WHEN CI applies a Klai diff against a pinned upstream source tree via `git apply --3way`, IF any hunk fails to apply cleanly, THEN the build SHALL fail with a non-zero exit code and an error message identifying the specific file and rejected hunk. The system SHALL NOT partially apply a diff, SHALL NOT silently skip a failed hunk, and SHALL NOT fall back to the previous artifact.

### REQ-3 — Upstream-native build toolchain
The system SHALL build `@librechat/agents`'s patched TypeScript sources using that repository's own documented build process (not a Klai-reimplemented bundler step). CI SHALL verify the expected build script exists before invoking it and SHALL fail loudly, with a message pointing at this SPEC's remediation notes, if the agents repository's build tooling has been restructured since the pin was last verified.

### REQ-4 — Klai-owned reproducible image
The system SHALL assemble a `ghcr.io/getklai/librechat:<upstream-tag>-klai.<n>` image `FROM` the pinned `ghcr.io/danny-avila/librechat:<upstream-tag>` base, with Lane A and Lane B build artifacts layered on top per the [Build pipeline design](#build-pipeline-design). `deploy/docker-compose.yml`'s `librechat-getklai` service and `klai-portal/backend/app/core/config.py::librechat_image` SHALL both reference the Klai-owned tag once a given surface's migration is verified on canary.

### REQ-5 — Build manifest with patched-file provenance
WHEN CI builds a `ghcr.io/getklai/librechat` image, the system SHALL record, per patched artifact: its upstream path, the diff file that produced it, and the resulting file's SHA256 inside the built image. This build manifest SHALL be published as a CI artifact and SHALL be the source of truth the deploy-time drift guard checks against, superseding the upstream-original-only hash check in today's `patch-manifest.txt`.

### REQ-6 — Deployed-artifact provenance verification
The system's deploy-time guard (successor to `check-patch-drift.sh`) SHALL verify that each patched file's SHA256 inside the image about to be deployed matches the value recorded in that image tag's CI build manifest (REQ-5), in addition to continuing to verify the image tag itself is explicitly pinned (not `:latest`/`:dev`/`:staging`). A mismatch SHALL block deployment.

### REQ-7 — Runtime transforms classified and justified individually
Each entrypoint runtime transform (inventory rows 7–8) SHALL carry an explicit, documented classification of MIGRATE or KEEP-RUNTIME with its rationale, rather than defaulting to runtime status by inertia. The Meili tenant-index transform (row 7) SHALL remain runtime because its target values are per-tenant and unknowable at image-build time. The client-polish transform (row 8) SHALL remain runtime because it is additive-only and independent of any compiled artifact by design. Both SHALL continue to be validated by a dry-run preflight that executes the transform against the CI-built image before deploy (coordinating with the in-flight work on `fix/librechat-preflight-feedback-config`, per [Relationship to in-flight preflight work](#relationship-to-in-flight-preflight-work)).

### REQ-8 — Canary-first migration with preserved rollback
Each of the six MIGRATE surfaces SHALL be migrated independently, one surface (or small logical group) at a time, deployed first to the `librechat-getklai` canary tenant before any production tenant. IF a migrated surface regresses on canary, THEN rollback SHALL be a single image-tag revert (back to the prior `ghcr.io/getklai/librechat:<tag>-klai.<n-1>` or the original `ghcr.io/danny-avila/librechat:<tag>` with the legacy bind-mount patch reinstated), consistent with the existing rollback story for LibreChat image changes.

### REQ-9 — No regression in existing guard coverage
The existing `deploy/librechat/tests/*.test.cjs` suite (gated by `.github/workflows/librechat-tests.yml`) SHALL continue to pass throughout the migration. Tests asserting behavior of a MIGRATE surface MAY be mechanically adapted to run against the new build pipeline's output instead of the old bind-mounted fixture, but SHALL NOT be deleted or weakened without an equivalent replacement assertion.

### REQ-10 — Upstreaming explicitly out of scope
The system SHALL NOT pursue upstreaming any of Klai's LibreChat or `@librechat/agents` modifications as GitHub PRs to `danny-avila/LibreChat` or `danny-avila/agents` as part of this SPEC. This was explicitly decided against on 2026-08-13 (see [Out of scope](#out-of-scope-explicit)).

## Relationship to in-flight preflight work

Branch `fix/librechat-preflight-feedback-config` (commit `81e6d7b78`, **not yet merged into `main`** as of this SPEC's authoring) is landing, independently of this SPEC:

- A dry-run-transform preflight (`deploy/librechat/dry-run-transforms.cjs`) that executes the Meili and (its version of) the feedback-forward transform against files extracted from the real pinned image, closing the "target exists but transform no longer applies" gap for those two runtime transforms.
- A **runtime-transform** re-wire of KB-feedback forwarding (`KB_FEEDBACK_NODE` heredoc block ported into both `klai-entrypoint.sh` and `getklai/entrypoint.sh`), deleting the dead `entrypoint.sh`/`patches/feedback.cjs`/`patches/feedback.patch` trio.
- A conservative `librechat.yaml` interface-config update for the 0.8.7 schema bump.

This SPEC's model treats KB-feedback forwarding (inventory row 6) as a MIGRATE surface — a source diff against `api/server/routes/messages.js` — which is a **different destination** than the runtime-transform approach in flight on that branch. Recommended sequencing (see [Open questions](#open-questions)): let the in-flight branch merge first to fix the currently-dead/inconsistent state quickly, then Phase 3 of this SPEC's migration (REQ-8) supersedes that runtime transform with the source-diff version once Lane B is proven on the five other surfaces. Shipping the preflight fix now and the structural migration later avoids blocking an already-in-flight, narrower fix behind a larger architecture change.

## Implementation phases

This SPEC is delivered incrementally; each phase is independently mergeable and does not require the next phase to be safe.

### Phase 1 — Spike: build pipeline plumbing, no behavior change
Stand up Lane A and Lane B build scripts against the *currently pinned* `v0.8.7` tag, producing artifacts byte-identical (or behaviourally equivalent, if bundler non-determinism prevents byte-identity) to the current `patches/*.cjs`/`*.js`/`*.ts` files. Resolve the open questions on `@librechat/agents` version-pin source and `packages/api`'s build step. No production traffic changes; goal is proving the pipeline works and quantifying build time.

### Phase 2 — CI workflow + build manifest — **DONE** (2026-08-14, PR #908)
Add `.github/workflows/librechat-image-build.yml`. Wire REQ-2 (fail-loud apply), REQ-3 (upstream toolchain), REQ-5 (build manifest). Push `ghcr.io/getklai/librechat:v0.8.7-klai.1` but do not yet point any compose service or provisioning path at it.

**Delivered.** `ghcr.io/getklai/librechat:v0.8.7-klai.1` exists
(digest `sha256:518c181f40e8…`); nothing points at it — the fleet still runs
`ghcr.io/danny-avila/librechat:v0.8.7` on all 42 tenants, and neither
`deploy/docker-compose.yml` nor `config.py::librechat_image` references the
Klai tag.

All five surfaces (rows 1–5) now have source diffs in
`deploy/librechat/patches-source/`. The three that Phase 1 did not cover
(`format.ts`, `stream.ts`, `share.js`) were derived by diffing the deployed
compiled patches against a pristine rebuild and translating the semantics back
to source.

Resolutions to the open questions:

- **Open question 1** (agents build invocation) — `npm ci && npm run build`,
  i.e. `rm -rf ./dist && tsdown && tsc -p tsconfig.build.json`. The ref is
  resolved at build time from `node_modules/@librechat/agents/package.json`
  inside the upstream image (v3.2.46 for LibreChat v0.8.7), never pinned
  independently.
- **Open question 2** (`packages/api` build) — a scoped build suffices:
  `build:data-provider` → `build:data-schemas` → `build:api`. No monorepo-wide
  build, no client bundle.
- **Open question 4** (where the manifest lives) — **inside the image** at
  `/klai-build-manifest.json`, plus an OCI label and a CI artifact. Self-
  describing, so REQ-6 cannot accidentally read a different tag's manifest.

Acceptance criterion note: byte-identity against the hand-maintained
`patches/*.cjs` was **rejected** as the bar. Those files were edited inside the
compiled bundle, so formatting differs by construction (helper placement, a
brace style the bundler collapses, one variable rename). The criterion is
behavioural — the existing guard tests run against the CI-built artifacts
extracted from the image, not against the repo snapshots. That is also the
criterion that would have caught the inert `createStreamServices` mount.

Drift guard: the patched-file list is owned by
`deploy/scripts/lib/librechat-patched-files.mjs`, and
`deploy/scripts/tests/librechat-patched-files.test.mjs` fails CI if the
Dockerfile's `COPY` lines disagree with it (the `url-shape-multi-file-drift`
class). Provenance logic is testable without a Docker daemon via
`KLAI_LIBRECHAT_EXTRACT_DIR`.

### Phase 2 — adversarial review outcome (2026-08-14, PR #917)

An external adversarial review confirmed the artifact claim (rebuilt-vs-deployed
differences are cosmetic; all five local build hashes matched CI) and found six
defects, three of them Phase 3 blockers. All fixed in #917:

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | HIGH | `v0.8.7-klai.1` was overwritten with a second digest | Tag carries the commit; workflow refuses to overwrite; digest printed for pinning |
| 2 | HIGH | Provenance check was self-attesting — a manifest claiming `v999-attacker` verified OK | Verification takes expected tag/ref/revision and re-hashes this checkout's diffs |
| 3 | MED | "Behaviour tests against the built image" covered 3 of 5 artifacts | `built_artifacts.test.cjs` adds search + index.cjs; a drift test keeps the step wired |
| 4 | MED | `sources: []` suppressed valid marker sources (`??` on a non-nullish empty array) | Only a non-empty list wins; pre-existing in the deployed patch, not introduced by Phase 2 |
| 5 | MED | `--depth 1` made `git apply --3way` degrade silently to direct application | `--filter=blob:none`; fallback is now fatal |
| 6 | LOW | Complete and partial marker grammars disagreed (whitespace, >8192 tails) | One grammar drives both; cap documented as a protocol contract at 65536 |

**Do not pin `ghcr.io/getklai/librechat:v0.8.7-klai.1`.** That tag is abandoned:
it points at one of two builds that were pushed over each other before finding 1
was fixed, and it cannot be deleted with the tokens available here. Phase 3 pins
the **digest** of an immutable `-<commit>` tag. The current one is
`ghcr.io/getklai/librechat:v0.8.7-klai.1-cc75acb0d37c`
(`sha256:7d8bb07626…`), built from main after #917.

The F4/F6 stream fixes deliberately did NOT go out to the bind-mounted `.cjs`
in production. Their tests assert against the built artifact, so those fixes
reach users through the Phase 3 canary — which is what a canary is for.

### Phase 3 — Migrate surfaces 1–5 to canary — **DONE** (2026-08-14, PRs #926 / #928)
Point `librechat-getklai`'s image at the Klai-owned tag. Verify against `deploy/librechat/tests/*.test.cjs` plus a new integration smoke test. Remove the corresponding entries from `deploy/librechat/patches/` and `patch-manifest.txt` once verified. Deploy-time drift guard upgraded to REQ-6 (patched-artifact provenance).

**Delivered.** `librechat-getklai` runs
`ghcr.io/getklai/librechat@sha256:360770c500…` (tag
`v0.8.7-klai.1-f415a2515817`). The other 41 tenants are untouched on
`ghcr.io/danny-avila/librechat:v0.8.7`.

Verified on the running canary: all five patches present in the image and no
patch bind-mounts left; `cleanupOnComplete` wired at both call sites; the
runtime cleanup transform correctly stood down ("already in place … skipping");
the build manifest readable at `/klai-build-manifest.json`; HTTP 200 from Caddy.
The two error lines in its log also occur on an upstream-image tenant, so they
predate this change.

Three things this phase added that the SPEC did not originally call for, each
because Phase 2's review exposed the need:

- **Digest-only pinning.** `deploy/check-klai-librechat-digest.sh` rejects any
  reference to our image that is not a digest — including a commit-suffixed
  tag. Immutable by convention is not immutable by construction.
- **Idempotent runtime transforms.** The entrypoint's cleanup-on-complete
  transform now stands down when it finds `CLEANUP_ON_COMPLETE` in the bundle,
  so an image carrying the source patch does not get a duplicate key layered on
  at boot. This is what makes Phase 5 a config change rather than an entrypoint
  edit per tenant.
- **Retirement in the safe order.** The mounts left the container definitions
  first; `deploy/librechat/getklai/patches/` and its rsync were deleted only
  after the canary was recreated without them, confirmed by
  `assert-safe-to-prune.sh`.

`getklai_v087_canary.test.cjs` is retired too. It re-ran the format and stream
assertions against the canary's *copies* of the patch files; those copies are
gone, and the same suites now run in `librechat-image-build.yml` against
artifacts extracted from the image the canary actually runs. Coverage moved and
tightened — it did not disappear.

`getklai/patch-manifest.txt` is retired: it pinned upstream hashes of files the
canary no longer mounts. Provenance for that container now comes from the build
manifest inside its image.

**Phase 5 note.** The fleet's provisioning default
(`config.py::librechat_image`) still points at upstream and is a TAG. When it
moves to the Klai image it must move to a digest, and
`check-klai-librechat-digest.sh` already covers that file.

### Phase 4 — Migrate surface 6 (feedback) — **DONE** (2026-08-14, PRs #936 / #937)
Land after `fix/librechat-preflight-feedback-config` merges. Replace its runtime `KB_FEEDBACK_NODE` transform with the Lane B source diff against `messages.js`. Delete the runtime heredoc block from both entrypoints.

**Delivered.** The canary runs
`ghcr.io/getklai/librechat@sha256:3b4fd8440c79…` (tag
`v0.8.7-klai.1-80df95854928`) — the first image carrying all six migrated
surfaces. Verified on the running container: `SPEC-KB-015` and the
`/internal/v1/kb-feedback` call present in `messages.js`, and BOTH runtime
transforms reporting that they stood down.

The diff was derived by running the entrypoint's own transform against a clean
v0.8.7 checkout and diffing the result, so it is byte-for-byte what the runtime
version produces. Round-trip verified against a second fresh clone.

No entrypoint change was needed: `messages.js` is COPYed rather than bundled, so
the `SPEC-KB-015` comment survives and the existing skip-check finds it. The
transform stays in both entrypoints for the 41 tenants still on upstream.

**The drift guard had to learn the same thing.** It dry-runs each runtime
transform against files extracted from the pinned image, which was correct while
every image was upstream. Against an image that already carries a patch it tests
a path that never runs at boot — feedback FAILED (its anchor had been consumed by
the very patch it would apply) and stream-cleanup PASSED while proving nothing.
Both now detect the baked-in marker and assert the behaviour instead.

### Canary moved to a tenant that is actually used (2026-08-14, PR #941)

getklai was the canary because it is the one LibreChat container declared in
`docker-compose.yml` — so "which tenant is the canary" was a consequence of how
a container happens to be managed, not a decision. It also serves almost no
traffic: one conversation between 9 July and today. A canary nobody uses cannot
produce the evidence Phase 5 is waiting for, no matter how long it runs.

Two capabilities were missing and now exist:

- `LIBRECHAT_IMAGE_OVERRIDES` (`slug=image[,slug=image]`, digest-only) lets a
  provisioning-managed tenant run a different image from the fleet default.
  Before this, the only lever was `settings.librechat_image` — all 42 or none,
  which is why the first canary had to be the compose-declared one.
- `POST /internal/librechat/regenerate?tenant=<slug>` applies to a single
  tenant, so trying an image on one tenant no longer restarts 42 containers.

Both are what a staged Phase 5 needs anyway: voys → a handful → the rest.

**Live:** `librechat-voys` runs
`ghcr.io/getklai/librechat@sha256:3b4fd8440c79…`; the other 40 remain on
upstream. Verified: both runtime transforms stand down, SPEC-KB-015 and
cleanup-on-complete baked in, HTTP 200, fewer error lines than a same-moment
upstream tenant. Rollback is deleting one compose line and recreating that
tenant.

### Phase 5 — Production rollout + retire full-file patches — **NOT STARTED**

Deliberately. The gate is not technical readiness — it is canary evidence, and
there is none worth the name yet: the canary has served no conversations since
the switch (it is Klai's own tenant and nobody has used it). Everything proven
so far is structural — right image, right files, right transforms standing down,
HTTP 200 — and structural evidence is exactly what a canary is NOT for.

What unblocks it: real conversations through `voys` (the canary as of PR #941),
covering the surfaces the diffs touch — a KB answer with sources rendered (stream), a thumbs-up/down
(messages), a web search (search), a shared link (share). Then flip
`config.py::librechat_image` to the digest and roll the fleet.

Note for that flip: `librechat_image` is currently a TAG. It must become a
digest; `check-klai-librechat-digest.sh` already covers that file and will fail
the build otherwise.

### Phase 5 — Production rollout + retire full-file patches
Promote the Klai-owned image from canary to all production tenants (`provisioning/infrastructure.py::_start_librechat_container`, `klai-portal/backend/app/core/config.py::librechat_image`). Confirm `deploy/librechat/patches/` contains only the two intentionally-runtime surfaces' supporting files (if any remain) and that `getklai/patches/` mirrors accordingly.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `danny-avila/agents`' build toolchain restructures again (as it did tsup→rolldown between LibreChat 0.8.6 and 0.8.7) | Medium | High | REQ-3: CI verifies the expected build script exists before invoking it and fails loudly with a specific remediation pointer rather than a generic build error. Pin the agents ref from LibreChat's own lockfile so a LibreChat bump and an agents-toolchain bump are always evaluated together, not independently. |
| Bundled (`dist/cjs`) output behaves subtly differently from raw source in edge cases (dead-code elimination, module-interop shims) that the guard tests don't cover | Low | Medium | Run the full `deploy/librechat/tests/*.test.cjs` suite AND a new integration smoke test against the actual CI-built image (not hand-crafted fixtures) before promoting past canary (Phase 3). |
| Build time cost — cloning + building two additional upstream repos on every upgrade | Medium | Low | Cache keyed on `(upstream-tag, diff-file-hashes)`; only rebuild when either changes, not on every unrelated PR touching `deploy/librechat/**`. |
| `packages/api`'s build step for `createStreamServices.ts` turns out to require LibreChat's full monorepo build (slow, high blast radius) rather than a scoped package build | Medium | Medium | Phase 1 spike resolves this before any REQ ships behind it; if the scoped build isn't feasible, this one surface can stay a source-diff-applied-then-directly-COPY-the-.ts-file approach matching today's mount, revisited once LibreChat's build process is better understood. |
| Diff no longer applies on every single upstream release, making this no better than the current manual-rediff cycle | Low | Medium | `git apply --3way`'s fuzzy/three-way merge tolerates unrelated nearby changes better than a byte-for-byte snapshot comparison; REQ-2's fail-loud, hunk-scoped error is strictly more actionable than today's whole-file SHA mismatch even in the worst case. |
| Migrating KB-feedback forwarding (row 6) collides with the in-flight `fix/librechat-preflight-feedback-config` branch, producing duplicate/conflicting transforms | Medium | Medium | Explicit sequencing in [Relationship to in-flight preflight work](#relationship-to-in-flight-preflight-work): Phase 4 starts only after that branch merges, and directly supersedes (deletes) its runtime transform rather than running both. |
| New `ghcr.io/getklai/librechat` image build workflow becomes a second thing that can silently drift from the compose/provisioning pins (same class of bug as `docker-compose-restart-vs-recreate`) | Low | High | REQ-6's deployed-artifact provenance check is exactly the mechanical guard for this; extend `check-patch-drift.sh`'s successor to also assert the compose image tag and `librechat_image` config default agree, mirroring the existing `validate_image_pin()` check. |

## Out of scope (explicit)

- **Upstreaming Klai's changes as PRs to `danny-avila/LibreChat` or `danny-avila/agents`.** Explicitly decided against on 2026-08-13: Klai's modifications (tenant-scoped Meili indexes, portal-api feedback forwarding, KB source/activity disclosure UI, public-share sanitization tuned to Klai's threat model) are Klai-specific product decisions, not general-purpose LibreChat improvements upstream would necessarily want, and maintaining an upstream relationship (issue triage, review cycles, potential rejection) is a materially different commitment than maintaining a private diff.
- **Migrating the Meili tenant-index transform or the client-polish injection to source-level** (inventory rows 7–8) — both explicitly justified as KEEP-RUNTIME per REQ-7.
- **Replacing the `-local-YYMMDD-HHMM` build pattern used by `deploy/crawl4ai/Dockerfile`** with this SPEC's model. That pattern (patch installed Python packages post-`pip install`, built and used locally on core-01, never pushed to a registry) solves a materially smaller problem (single Python patch, no compiled-bundle drift) and is out of scope here.
- **Changing how upstream tag bumps themselves are decided or reviewed.** Bumping the pinned `danny-avila/LibreChat` tag remains a deliberate, human-reviewed action in all three pin locations, same as today.
- **General CI cost/caching strategy beyond what's needed for this pipeline.** Broader CI infrastructure changes are a separate concern.

## Open questions

1. **Exact `@librechat/agents` build invocation.** Believed to be a rolldown-driven `npm`/`pnpm` script but not yet confirmed against the actual pinned-tag source tree. Resolve in Phase 1.
2. **`packages/api`'s build step for `createStreamServices.ts`.** Does LibreChat's official Docker build compile `packages/api` via a scoped `tsup`/`tsc` invocation during image build, or does it ship a pre-built `dist/` that needs a separate, potentially monorepo-wide build command? Resolve in Phase 1 by inspecting the pinned tag's own `Dockerfile` and build scripts directly — do not assume.
3. **Sequencing against `fix/librechat-preflight-feedback-config`.** This SPEC recommends letting that branch merge first (fixes the currently dead/inconsistent feedback-forwarding state and adds the dry-run preflight quickly) and having Phase 4 supersede its runtime transform later. Confirm this ordering is acceptable before Phase 4 begins — the alternative (blocking that branch until this SPEC's Lane B is ready) delays a fix that's already scoped and reviewed.
4. **Where does the build manifest (REQ-5) live and how does the deploy-time guard (REQ-6) fetch it?** Candidates: an OCI image label baked into the built image (self-describing, no separate storage), a companion JSON artifact pushed alongside the image tag, or a row in an existing deploy-tracking system. Not yet decided; affects REQ-6's implementation but not its acceptance criteria.
5. **Tag-suffix increment (`-klai.<n>`) ownership.** Manual bump per PR, or CI-computed from the diff-file-hash so identical diffs against the same upstream tag are idempotent and don't need a human to remember to bump `<n>`? Leaning toward CI-computed for idempotency, not yet decided.

## References

- `deploy/librechat/patch-manifest.txt` — current SHA-pinned manifest (5 entries) this SPEC's build manifest (REQ-5) supersedes
- `deploy/librechat/check-patch-drift.sh` — current drift guard, including `validate_runtime_targets()` added by the 2026-08-13 incident fix
- `deploy/librechat/klai-entrypoint.sh`, `deploy/librechat/getklai/entrypoint.sh` — current runtime transforms (Meili tenant-index rewrite, client polish)
- `deploy/librechat/entrypoint.sh`, `deploy/librechat/patches/feedback.cjs`, `deploy/librechat/patches/feedback.patch` — currently-dead SPEC-KB-015 feedback carrier (inventory row 6)
- `deploy/librechat/tests/*.test.cjs` — existing guard test suite, gated by `.github/workflows/librechat-tests.yml`
- `.github/workflows/deploy-librechat-config.yml` — current sync-to-core-01 workflow for the base config and bind-mounted patches
- `.github/workflows/portal-api.yml` — precedent for the `docker/build-push-action` + `ghcr.io/getklai/*` pattern this SPEC's new workflow follows
- `deploy/crawl4ai/Dockerfile` — smaller-scale precedent for "Klai variant built `FROM` an upstream base image," differs in patching installed packages post-build rather than diffed source
- `klai-portal/backend/app/core/config.py::librechat_image`, `klai-portal/backend/app/services/provisioning/infrastructure.py::_start_librechat_container` — production image-pin consumers
- `deploy/docker-compose.yml` — `librechat-getklai` canary service definition
- Git history of the 2026-08-13 upgrade: `80a95da5e` → `307643ad7` (revert) → `efd07680e` → `65caf3586` (revert) → `e0e90cc77` (revert with root cause) → `1d6131070`/`5cd077b3d`/`9c8ede602` (rebase attempts) → `5e041bcb2` (landed) → `4fdc93544`/`874fb9ffb` (follow-up fixes) → `81e6d7b78` (unmerged preflight/feedback branch)
- Upstream repos: `danny-avila/agents` (source of `src/messages/format.ts`, `src/stream.ts`, `src/tools/search/search.ts`), `danny-avila/LibreChat` (source of `api/server/routes/share.js`, `packages/api/src/stream/createStreamServices.ts`, `api/server/routes/messages.js`)
