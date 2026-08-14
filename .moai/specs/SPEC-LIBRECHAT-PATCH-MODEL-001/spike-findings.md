---
id: SPEC-LIBRECHAT-PATCH-MODEL-001-phase1-spike
phase: 1
status: complete
date: "2026-08-14"
author: Claude (spike agent)
branch: spike/librechat-source-patch-model
---

# Phase 1 spike findings: source-level LibreChat patch model

## TL;DR / Go-no-go

**GO.** The target model is buildable and fully reproducible from source for
every surface this spike could test. Both build lanes were empirically
verified to produce **byte-identical** artifacts to what ships in the real
`ghcr.io/danny-avila/librechat:v0.8.7` production image, using only `npm ci`
+ the upstream repos' own documented build scripts — no bundler
reverse-engineering, no hand-tuning.

A structural bug was also found in the **currently deployed** model (not
introduced by this spike): the `createStreamServices.ts` bind-mount patch
(inventory row 5) is almost certainly **inert in production** — see
[Unplanned finding](#4-unplanned-finding-createstreamservicests-bind-mount-is-inert-in-production)
below. This is evidence *for* migrating to the source-diff model (a
build-time patch cannot silently no-op the way a stale bind-mount can), but
it is also a live gap in current Klai behavior that the team should be aware
of independent of this SPEC.

## 1. Reproducibility verdict (the crux question)

**Verdict: fully reproducible, byte-identical, both lanes.**

| Artifact | Lane | SHA256 (rebuilt from source) | SHA256 (extracted from real `ghcr.io/danny-avila/librechat:v0.8.7`) | Match |
|---|---|---|---|---|
| `@librechat/agents` `dist/cjs/messages/format.cjs` | A | `b4b6e97767df7019df0bd18a94e9c6207e5272725e1313901f68240e7c4b221a` | `b4b6e97767df7019df0bd18a94e9c6207e5272725e1313901f68240e7c4b221a` | **identical** |
| `@librechat/agents` `dist/cjs/stream.cjs` | A | `a1cde9ccc350eea7852429cce6405a8bc6368cd77216980048f3d43dd5c6fade` | `a1cde9ccc350eea7852429cce6405a8bc6368cd77216980048f3d43dd5c6fade` | **identical** |
| `@librechat/agents` `dist/cjs/tools/search/search.cjs` | A | `a64375fbcbc1acc4b0659e966854f7a9c237b2bf45de518c119eff543b800d2c` | `a64375fbcbc1acc4b0659e966854f7a9c237b2bf45de518c119eff543b800d2c` | **identical** |
| `packages/api` `dist/index.cjs` (unmodified) | B | `adf2bf846dd897996a9c4c1caa047c43639887faea5a0bb098f0ae7b94187f52` | `adf2bf846dd897996a9c4c1caa047c43639887faea5a0bb098f0ae7b94187f52` | **identical** |

All three Lane A hashes also exactly match the `expected_upstream_sha256`
values already pinned in `deploy/librechat/patch-manifest.txt`, confirming
the manifest's existing pins are correct and that this spike is comparing
against the same upstream-original baseline Klai already trusts.

`api/server/routes/share.js` and (once it exists) `api/server/routes/messages.js`
were not independently rebuilt in this spike (Lane B row 4/6 — "no build
step, direct COPY" per the SPEC's own inventory) since they need no
compilation; extracting them and confirming their location was sufficient to
validate the model for those two rows.

### How reproducibility was proven

1. Resolved the pinned `@librechat/agents` version by reading
   `node_modules/@librechat/agents/package.json` out of the real
   `ghcr.io/danny-avila/librechat:v0.8.7` image: **`"version": "3.2.46"`**.
   This exactly matches the tag `v3.2.46` in `danny-avila/agents`, and
   matches the `"@librechat/agents": "^3.2.46"` peerDependency pin in
   `danny-avila/LibreChat`'s own `packages/api/package.json` — so the
   SPEC's stated goal ("pin the agents ref from LibreChat's own lockfile
   /peer-dep declaration, not an independently floating pin") is already
   satisfiable mechanically: read it out of `packages/api/package.json`'s
   peerDependencies, or equivalently out of the shipped
   `node_modules/@librechat/agents/package.json`.
2. `git clone` + `git checkout v3.2.46` on `danny-avila/agents`, `npm ci`,
   `npm run build` (unmodified) → compared `dist/cjs/{messages/format,stream,tools/search/search}.cjs`
   against the same paths extracted read-only from the real production
   image. Byte-identical, all three.
3. Same exercise for Lane B: `git clone --branch v0.8.7 --depth 1`
   `danny-avila/LibreChat`, `npm ci` at the monorepo root, then
   `npm run build:data-provider && npm run build:data-schemas && npm run build:api`
   (the scoped subset of the Dockerfile's `npm run frontend`, see
   [Open question 2](#open-question-2-packagesapis-build-step) below) →
   compared `packages/api/dist/index.cjs` against the same path extracted
   from the real image. Byte-identical.

No bundler nondeterminism, no version skew, no build-flag differences were
observed anywhere. `git apply --3way` (REQ-2's stated apply mechanism)
applied both prototype diffs cleanly with zero fuzz on the first try.

## 2. Working build commands (verbatim)

### Extracting ground-truth files from the real production image (read-only)

Docker credential helper (`osxkeychain`) blocked `docker pull`/`docker run`
in this non-interactive environment (see
[What was blocked](#6-what-was-genuinely-blocked) below) — the labeled
`docker run --entrypoint cat` extraction the task asked for could not run.
The equivalent read-only extraction was done directly against the GHCR OCI
Registry API instead (anonymous pull token, no credentials, read-only,
touches nothing running):

```bash
# Anonymous pull token (public image, no auth needed)
curl -s "https://ghcr.io/token?scope=repository:danny-avila/librechat:pull&service=ghcr.io" \
  -o /tmp/ghcr-token.json
TOKEN=$(python3 -c "import json;print(json.load(open('/tmp/ghcr-token.json'))['token'])")

# OCI index -> amd64 manifest -> layer digest list
curl -sL -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  "https://ghcr.io/v2/danny-avila/librechat/manifests/v0.8.7" -o index.json
# pick the linux/amd64 entry's digest, fetch that manifest:
curl -sL -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  "https://ghcr.io/v2/danny-avila/librechat/manifests/sha256:<amd64-digest>" -o amd64.json

# node_modules/@librechat/agents/* lives in the single largest layer
# (731 876 178 bytes, digest sha256:d784a973...). Locate it by listing
# each layer's tar contents (curl | tar -tzf -), then targeted-extract:
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/danny-avila/librechat/blobs/sha256:d784a9733c41a5ae2adad0f1de75b1204f2c273e6d4e936eec5b587cb0abe20c" \
  | tar -xzf - -C ./extract \
      app/node_modules/@librechat/agents/package.json \
      app/node_modules/@librechat/agents/dist/cjs/messages/format.cjs \
      app/node_modules/@librechat/agents/dist/cjs/stream.cjs \
      app/node_modules/@librechat/agents/dist/cjs/tools/search/search.cjs \
      app/node_modules/@librechat/api   # reveals the workspace symlink, see finding below

# api/server/routes/share.js + packages/api/src/stream/createStreamServices.ts
# live in a much smaller layer (7 638 219 bytes, digest sha256:8910d5fa...):
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/danny-avila/librechat/blobs/sha256:8910d5fafdbe5b184a675ea38d0667f225c28a3cda05c245c980a774a6efbe93" \
  | tar -xzf - -C ./extract app/api/server/routes/share.js app/packages/api/src/stream/createStreamServices.ts

# packages/api/dist/index.cjs (the built artifact actually loaded at runtime)
# lives in yet another layer (23 407 348 bytes, digest sha256:83d6d3da...):
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/danny-avila/librechat/blobs/sha256:83d6d3daad872a935a01c55479d38bbf8cc9c3d9725be5c5c0af6a833e09a6af" \
  | tar -xzf - -C ./extract app/packages/api/dist/index.cjs

# packages/api/package.json (proves main -> dist/index.cjs) lives in a
# tiny, separate layer (2 064 bytes, digest sha256:5d9f37d9...):
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/danny-avila/librechat/blobs/sha256:5d9f37d99b4aff8d2e6f0a77951ca9278ebd092ba64e34c6b40c37fa587de3a4" \
  | tar -xzf - -C ./extract app/packages/api/package.json
```

Full SHA256 layer-digest → file-path map for all 18 unique layers of
`ghcr.io/danny-avila/librechat:v0.8.7` (linux/amd64) is reconstructable from
the commands above; keeping the manifest cached avoids re-scanning
per-file. This mechanism (anonymous GHCR pull + targeted `tar -x` of just
the needed paths) is what Phase 2's CI-side artifact-provenance check
(REQ-6) should also use — it is faster and lighter than a full `docker
pull`, needs zero credentials for public images, and CI runners typically
already have equivalent registry auth for private images if Klai ever
moves `ghcr.io/getklai/librechat` to a private repo.

### Lane A — `@librechat/agents`

```bash
git clone https://github.com/danny-avila/agents.git agents-upstream
cd agents-upstream
git checkout v3.2.46          # resolved from node_modules/@librechat/agents/package.json in the real image
npm ci                        # 925 packages, ~9s
npm run build                 # == "rm -rf ./dist && tsdown && tsc -p tsconfig.build.json"
# -> dist/cjs/messages/format.cjs, dist/cjs/stream.cjs, dist/cjs/tools/search/search.cjs
```

Confirmed `package.json` `"build"` script:
`rm -rf ./dist && tsdown && tsc -p tsconfig.build.json`. `tsdown@0.22.2`
directly depends on `rolldown@1.1.0` and `rolldown-plugin-dts` (confirmed
via `node_modules/tsdown/package.json` and the build log's own banner:
`tsdown v0.22.2 powered by rolldown v1.1.0`) — the SPEC's "believed to be a
rolldown-driven npm/pnpm script" guess is **confirmed correct**, no
correction needed. Node engine requirement `>=24.0.0` (local Node
v24.15.0 satisfied it).

Applying and rebuilding the `search.ts` diff (see [section 3](#3-search-patch-proven-end-to-end)):

```bash
git apply --3way /path/to/search.ts.patch    # applies cleanly, zero fuzz
npm run build                                 # rebuild, ~230ms incremental
```

### Lane B — LibreChat app-source overlay

```bash
git clone --branch v0.8.7 --depth 1 https://github.com/danny-avila/LibreChat.git librechat-upstream
cd librechat-upstream
npm ci --no-audit              # root workspace install: 3011 packages, ~38s
npm run build:data-provider    # tsdown, ~95ms — packages/api peer-deps on this
npm run build:data-schemas     # tsdown, ~510ms — packages/api peer-deps on this too
npm run build:api              # == "cd packages/api && npm run clean && tsdown"
# -> packages/api/dist/index.cjs (2.01 MB, byte-identical to the shipped image)
```

Applying and rebuilding the `createStreamServices.ts` diff:

```bash
git apply --3way /path/to/createStreamServices.ts.patch   # applies cleanly, zero fuzz
npm run build:api                                           # rebuild, ~470ms
```

## 3. Search patch proven end-to-end

`deploy/librechat/patches/search.cjs` (Klai's currently-deployed compiled
patch) diffed against the pristine rebuilt `search.cjs` resolves to exactly
four semantic changes, matching the SPEC's inventory row 3 description:

1. `chunker.cleanText` — strips lone UTF-16 surrogates after the existing
   whitespace normalization (LinkedIn-style bold/italic Unicode text can
   encode as surrogate pairs; a naive text-splitter can cut inside a pair).
2. `chunker.splitText` — same surrogate-stripping applied per-chunk after
   `RecursiveCharacterTextSplitter.splitText`.
3. `getHighlights` + `createSourceProcessor`: `topResults` default `5 -> 3`.
4. Serper + SearXNG `getSources`: `numResults` default `8 -> 5`.

The minimal TypeScript source diff expressing this (against
`src/tools/search/search.ts` in `danny-avila/agents`) is committed at
[`patches-source/search.ts.patch`](./patches-source/search.ts.patch).

Verification chain, all with concrete evidence (not "should work"):

1. **Diff applies cleanly**: `git apply --3way --check` and then a real
   `git apply --3way` against a fresh `v3.2.46` checkout, both zero fuzz,
   zero conflicts (`Applied patch to 'src/tools/search/search.ts' cleanly.`).
2. **Rebuild succeeds**: `npm run build` completes in ~230ms, same file set
   (282 files, 4.08 MB total) as the unmodified build.
3. **Built output carries Klai's behavior** — grepped the rebuilt
   `dist/cjs/tools/search/search.cjs`:
   ```
   $ grep -c "klai-patch] search" dist/cjs/tools/search/search.cjs
   2
   $ grep -n "topResults = 3\|numResults = 5" dist/cjs/tools/search/search.cjs
   62:const getHighlights = async ({ query, content, reranker, topResults = 3, ... }) => {
   91:	const getSources = async ({ query, date, country, safeSearch, numResults = 5, type }) => {
   151:	const getSources = async ({ query, numResults = 5, safeSearch, type }) => {
   273:	const { topResults = 3, reranker, logger } = config;
   ```
4. **Semantic equivalence to the currently-deployed patch**: `diff -u` between
   the freshly-rebuilt `search.cjs` and Klai's current
   `deploy/librechat/patches/search.cjs` shows *zero* logic differences —
   the only lines that differ are comment style (`/** JSDoc block */` in
   the rebuilt output, matching what `tsdown`/rolldown emits from the
   source's block comments, vs. hand-written `//` line comments in the
   existing hand-crafted `.cjs` snapshot) and one intermediate variable
   name (`cleaned` vs `cleanedSpaces`). Both are cosmetic — regex patterns,
   warning message strings, and all four numeric defaults are identical
   token-for-token.

This is the strongest form of "prove one patch end-to-end" available short
of running the built image in a live container: same regex, same warning
text, same tuned defaults, reproduced via `git apply --3way` against
upstream source and upstream's own build toolchain, with zero manual
hand-editing of compiled output.

## 4. Unplanned finding: `createStreamServices.ts` bind-mount is inert in production

This was not something the spike set out to find — it fell out of
investigating [open question 2](#open-question-2-packagesapis-build-step)
(does `packages/api` need its own compile step?). The answer to that
question exposed a structural problem with the **currently deployed**
patch for inventory row 5, independent of anything this SPEC proposes to
change.

**The chain of evidence:**

1. `deploy/docker-compose.yml` line 405 bind-mounts:
   ```
   ./librechat/getklai/patches/createStreamServices.ts:/app/packages/api/src/stream/createStreamServices.ts:ro
   ```
   — i.e. it overwrites the **TypeScript source file**.
2. `api/server/index.js` (LibreChat's actual server entry point) does:
   ```js
   const {
     ...
     createStreamServices,
     ...
   } = require('@librechat/api');
   ...
   const streamServices = createStreamServices();
   ```
   — a **package-name** require, not a path into `packages/api/src`.
3. Extracting `node_modules/@librechat/api` from the real image shows it is
   a **symlink**: `api -> ../../packages/api` (standard npm-workspaces
   linking).
4. `packages/api/package.json`: `"main": "dist/index.cjs"` — so
   `require('@librechat/api')` resolves through the symlink to
   `packages/api/dist/index.cjs`, **not** `packages/api/src/*.ts`.
5. Nothing in `deploy/librechat/klai-entrypoint.sh` or
   `getklai/entrypoint.sh` rebuilds `packages/api` after container start
   (verified: no `tsdown`, `tsc`, `npm run build`, or `packages/api`
   reference in either entrypoint).
6. Therefore: `require('@librechat/api')` at runtime always resolves to
   whichever `dist/index.cjs` LibreChat's own image build produced —
   Klai's bind-mounted `.ts` source is never compiled, never loaded, and
   has **no observable effect** on the running server.

**Concrete before/after proof** (built empirically in this spike, not
inferred):

```
$ shasum -a 256 packages/api/dist/index.cjs   # unmodified rebuild
adf2bf846dd897996a9c4c1caa047c43639887faea5a0bb098f0ae7b94187f52
$ # == the SHA of dist/index.cjs extracted from the real, currently-deployed image

$ git apply --3way createStreamServices.ts.patch && npm run build:api
$ shasum -a 256 packages/api/dist/index.cjs   # patched rebuild
bef240405524b352fa8d9869bc848b0952a7ef122827b840341915fcd5c9bab8
$ # DIFFERENT hash — a correctly-migrated build changes the artifact
$ #                  that Klai's current bind-mount patch does not touch
```

**Why this matters semantically, not just structurally**: upstream's
`GenerationJobManager.ts` (the actual consumer) does
`this._cleanupOnComplete = services.cleanupOnComplete ?? true` — i.e. if
`createStreamServices()`'s return value has no `cleanupOnComplete` field
(which is the case for the pristine/currently-running `dist/index.cjs`,
confirmed via `grep -c cleanupOnComplete` returning hits only in
`GenerationJobManager`, none in an unpatched `createStreamServices`
build), it **defaults to `true`** — immediate cleanup, the opposite of
what Klai's patch (`CLEANUP_ON_COMPLETE = false`) intends. The SPEC's
inventory row 5 label is "Completed-generation-job retention" — as far as
this spike's static analysis can tell, **that retention behavior is not
currently active in production**, because the file that would activate it
is bind-mounted somewhere the running process never reads.

**What this spike could NOT verify**: this is static/build-time analysis
(file-resolution graph + hash comparison), not a live-container runtime
observation. It's possible some other mechanism reloads `packages/api` at
runtime that this spike didn't find (no evidence of one was found in
`klai-entrypoint.sh`/`getklai/entrypoint.sh`, and Node's `require()` cache
does not self-invalidate on file changes without an explicit watch/reload
setup, which is absent in a production `NODE_ENV=production` container).
**Recommend**: verify with production evidence — a VictoriaLogs query for
generation-job lifecycle timing (does a completed job's cleanup happen
immediately or after Klai's intended retention window?) would confirm or
falsify this independent of anything else in this SPEC. This is flagged
here rather than fixed here — Phase 1 of this SPEC is explicitly
investigation-only, no production changes, and the fix (migrating row 5
to a real source-diff via the Lane B pipeline, which this spike already
demonstrates works) is exactly what Phase 3 of this SPEC already plans to
do. This finding is a strong *additional* argument for prioritizing row 5's
migration, on top of the SPEC's existing provenance rationale.

## 5. Answers to the SPEC's open questions

### Open question 1: exact `@librechat/agents` build invocation

**Resolved, SPEC's guess confirmed correct.** `npm run build` ==
`rm -rf ./dist && tsdown && tsc -p tsconfig.build.json`. `tsdown@0.22.2` is
directly rolldown-based (`rolldown@1.1.0`, confirmed via both
`node_modules/tsdown/package.json` dependencies and the build's own log
banner). No correction to the SPEC needed — Section
"Build pipeline design" step 4's "currently believed to be a rolldown-driven
npm/pnpm script" can be promoted from "believed" to "confirmed" with
`npm run build` as the exact invocation.

### Open question 2: `packages/api`'s build step

**Resolved via direct inspection of the pinned tag's own `Dockerfile` and
root `package.json`** (per the SPEC's own instruction not to assume):

- `Dockerfile` (root, single-stage): after `npm ci` at the workspace root,
  runs `npm run frontend`.
- Root `package.json`:
  `"frontend": "npm run build:data-provider && npm run build:data-schemas && npm run build:api && npm run build:client-package && cd client && npm run build"`.
- `"build:api": "cd packages/api && npm run build"`, and
  `packages/api/package.json`'s own `"build": "npm run clean && tsdown"`.

So: **yes, `packages/api` has its own scoped build step** (`tsdown`, same
tool as Lane A), and it is NOT a full-monorepo build — the SPEC's stated
risk ("might require LibreChat's full monorepo build, slow, high blast
radius") does **not materialize**. The scoped subset Klai's CI needs is
`npm run build:data-provider && npm run build:data-schemas && npm run build:api`
(skipping `build:client-package` and the React client build entirely,
since those aren't consumed by any Klai patch). This was empirically
verified in this spike: root `npm ci` took ~38s (3011 packages, 48 MB
shallow clone), and the three scoped build steps completed in under 1.1s
combined. `build:data-provider` and `build:data-schemas` must run before
`build:api` because `packages/api` has workspace peer-dependencies on
`librechat-data-provider` and `@librechat/data-schemas` resolved via the
same npm-workspaces symlinking mechanism found in the
[unplanned finding](#4-unplanned-finding-createstreamservicests-bind-mount-is-inert-in-production)
above.

**Correction to the SPEC's risk table**: the row "`packages/api`'s build
step for `createStreamServices.ts` turns out to require LibreChat's full
monorepo build (slow, high blast radius) rather than a scoped package
build" — likelihood/impact can be downgraded from Medium/Medium to
resolved-favorably: it needs the full monorepo's `npm ci` (fast, ~40s) but
only a 3-step *scoped* build (`build:data-provider`, `build:data-schemas`,
`build:api`), not the full `npm run build` (turbo, everything) or
`npm run frontend` (also builds the React client).

### Open question 3: sequencing against `fix/librechat-preflight-feedback-config`

Not directly this spike's job to decide, but the premise was checked and
still holds: `fix/librechat-preflight-feedback-config` (commit `81e6d7b78`)
is confirmed **still unmerged into `main`**
(`git merge-base --is-ancestor 81e6d7b78 origin/main` → not an ancestor,
checked 2026-08-14). The SPEC's recommended ordering (let it merge first,
Phase 4 supersedes its runtime transform later) remains the right call —
nothing in this spike's findings changes that recommendation. One
additional data point in its favor: this spike's Lane B build pipeline is
now proven to work end-to-end for `packages/api`-hosted TS sources, so
Phase 4's supersession of the runtime `KB_FEEDBACK_NODE` transform with a
Lane B diff against `api/server/routes/messages.js` (an already-unbundled
JS file, same class as `share.js`, confirmed to need **no** compile step)
has no remaining unknowns from the build-pipeline side.

### Open question 4: where the build manifest lives

**Recommendation: OCI image label, not a companion artifact.** The build
manifest (REQ-5: per-patched-artifact upstream path, diff file, resulting
SHA256) should be baked into the built image as an OCI label
(`org.opencontainers.image.*` custom annotation or a Klai-namespaced
label, e.g. `io.getklai.librechat.patch-manifest`), containing the JSON
manifest as a label value (or a content-addressed reference to it, if the
manifest itself grows past a reasonable label-size limit). Rationale:

- **Self-describing**: `docker inspect` / `skopeo inspect` / the GHCR
  manifest API (used throughout this spike without any Docker daemon)
  can read it directly, with zero additional storage or lookup
  infrastructure. This spike's own read-only extraction workflow (curl +
  registry API, no `docker pull` needed) is a direct preview of what
  REQ-6's deploy-time drift guard could look like: fetch the OCI config
  blob (already fetched via `GET /v2/.../manifests/<digest>` →
  `config.digest` → `GET /v2/.../blobs/<config-digest>`), read the label,
  compare recorded SHA256 against a fresh targeted-extraction of the
  actual patched paths — no full image pull required even for the
  drift check itself.
- A companion JSON artifact (pushed alongside the tag, e.g. as a second
  OCI artifact or a GitHub Actions build artifact) is a *weaker*
  provenance story: it can drift from the image it claims to describe if
  the two pushes aren't atomic, which is exactly the "provenance is
  unverifiable" class of gap this SPEC exists to close (see the SPEC's
  own "What this cost on 2026-08-13" section). A label baked into the
  same image manifest push cannot drift from the image by construction.
- A row in a separate deploy-tracking system adds an external dependency
  and a new "what if that system is down/stale" failure mode for
  something REQ-6 needs to check on every deploy.

Concrete implementation sketch: `docker buildx build --label
io.getklai.librechat.patch-manifest="$(cat build-manifest.json | base64)"`
(or `--label-file` if buildx supports it directly — verify against the
CI runner's buildx version in Phase 2) at final-assembly time, using
values collected from both lanes' build steps in the same CI job.

### Open question 5: `-klai.<n>` tag-suffix ownership

**Recommendation: CI-computed, agrees with the SPEC's stated leaning.**
Compute `<n>` as a short hash of the concatenated diff-file contents
(e.g. `sha256sum patches-source/*.patch | sha256sum | cut -c1-8`) rather
than an incrementing integer a human has to remember to bump. Rationale
beyond idempotency (already the SPEC's stated reason):

- An incrementing integer requires CI to know "what was the last `<n>`
  for this upstream tag", which means either querying the registry's
  tag list (extra API call, race condition if two PRs build concurrently)
  or storing state somewhere (another provenance surface to keep in
  sync — same class of problem REQ-5/REQ-6 exist to eliminate elsewhere).
- A content-derived suffix is trivially reproducible by anyone with the
  diff files and no CI access — useful for local debugging ("does this
  diff set match what's deployed?") without hitting the registry at all.
- Collision risk is effectively zero (8 hex chars of SHA256 over a small,
  reviewed diff set) and a human-readable git-diff-derived suffix (vs. an
  opaque incrementing counter) makes `ghcr.io/getklai/librechat:v0.8.7-klai.a1b2c3d4`
  self-evidently tied to a specific diff-file state, which a reviewer can
  verify locally by re-hashing `patches-source/*.patch`.

One caveat: this changes tag *readability* (not "the 3rd revision" but
"a hash") — if human readability of "which revision am I on" matters for
manual ops (rollback commands, etc.), consider a hybrid:
`v0.8.7-klai.<n>-<short-hash>` where `<n>` is still CI-tracked via a
simple counter file committed alongside the patches (bumped in the same
PR that changes a patch file, so it's still human-reviewed and
git-diffable, just not silently-forgotten the way an implicit "last tag
pushed" lookup would be).

## 6. What was genuinely blocked

- **`docker run --entrypoint cat ghcr.io/danny-avila/librechat:v0.8.7 <path>`
  (the labeled read-only extraction the task specified) could not run.**
  `docker pull` failed non-interactively with
  `error getting credentials - err: exit status 1, out: User canceled the
  operation. (-128)` from the macOS `osxkeychain` credential helper, even
  with `DOCKER_CONFIG` pointed at an empty/anonymous config
  (`{"auths": {}}`), both via env var and `--config` flag, both for
  `docker pull` and `docker manifest inspect` variants that touch blob
  data (`docker manifest inspect` itself worked fine — only blob-fetching
  operations triggered the keychain prompt). This appears to be an
  OrbStack-specific credential-resolution path that ignores
  `DOCKER_CONFIG`/`--config` for the actual pull/blob-fetch step even
  though it honors it for manifest-only calls; a macOS Keychain access
  prompt requires interactive approval this environment cannot provide.
  **Workaround used**: direct GHCR OCI Registry API calls via `curl`
  (anonymous pull token, no credentials) — read-only, touches nothing
  running, and arguably a *better* mechanism for CI to use anyway (see
  [open question 4](#open-question-4-where-the-build-manifest-lives)'s
  recommendation). No production system was touched; this was purely a
  local tooling constraint.
- Everything else the task asked for was completed. Nothing else was
  blocked.

## 7. Corrections this spike found for the SPEC itself

1. **Section "Open questions" item 1 and item 2** can both move from
   "believed"/"not yet confirmed" to confirmed, with the exact invocations
   documented in [section 5](#5-answers-to-the-specs-open-questions)
   above.
2. **Risks table row** "`packages/api`'s build step ... turns out to
   require LibreChat's full monorepo build" — downgrade from Medium/Medium
   risk to low-risk-resolved; it needs the monorepo's fast `npm ci` but
   only a 3-step scoped build.
3. **No correction needed** to the Build pipeline design's Lane A/Lane B
   structure, the final-assembly `Dockerfile` sketch, or any of the
   REQ-1 through REQ-10 requirements — everything in those sections
   matches what this spike found empirically.
4. **New finding not previously in the SPEC** (see section 4): the
   currently-deployed `createStreamServices.ts` bind-mount patch appears
   to be inert. This isn't something the SPEC needs to *change* — Phase 3
   of the existing plan already migrates this surface to a working
   source-diff — but it's worth a one-line callout in the SPEC's
   "Current-state inventory" table (row 5) or "Risks and mitigations"
   section so the team understands the migration for this specific
   surface is not just "safer provenance" but "makes a currently-broken
   feature work," which may affect how Phase 3's surfaces get
   prioritized/sequenced relative to each other.

## 8. Residual risk / what wasn't verified

- **No live container was booted.** All verification is
  build-artifact/hash-level (byte-identical `.cjs`/`.cjs` output) and
  static require-graph analysis (symlink + `package.json` main field +
  grep'd `require()` call site), not an actual running LibreChat server
  observed end-to-end. This matches the task's Phase 1 scope ("no
  production traffic changes") but means the `createStreamServices.ts`
  finding in particular should get a live/log-based confirmation before
  anyone treats it as fully proven.
- **`api/server/routes/messages.js`** (inventory row 6, feedback
  forwarding) was not diffed or build-tested in this spike — it's
  currently a **dead** surface per the SPEC's own inventory ("orphaned
  entrypoint.sh... referenced by no deployment path"), and Phase 4
  explicitly sequences after `fix/librechat-preflight-feedback-config`
  merges, so there was no live patch to compare against yet. The
  build-pipeline mechanism (Lane B, no-build-step direct COPY, same as
  `share.js`) is already proven by this spike to work for that file
  *class*; only the specific diff content remains to be written once
  that branch lands.
- **CI cost at scale wasn't measured** beyond this spike's single-run
  timings (Lane A: `npm ci` ~9s + build ~250ms; Lane B: `npm ci` ~38s +
  3-step scoped build ~1.1s). Phase 2's caching strategy
  (`(upstream-tag, diff-file-hashes)` per the SPEC's risk table) should
  still be implemented, but the raw uncached cost looks low enough
  (under a minute total, both lanes) that caching is a nice-to-have for
  CI minutes, not a blocker for correctness.
- **OCI label size limits for the build manifest** (open question 4's
  recommendation) weren't verified against actual Docker/buildx/GHCR
  limits in this spike — Phase 2 should confirm the manifest JSON fits
  comfortably under whatever limit applies (Docker labels are typically
  fine well past what 6 small file records need, but this should be a
  concrete check, not an assumption).

## 9. Artifacts produced by this spike

- [`patches-source/search.ts.patch`](./patches-source/search.ts.patch) —
  minimal source diff against `danny-avila/agents` `src/tools/search/search.ts`,
  verified to apply via `git apply --3way` and rebuild to a
  behaviorally-equivalent (semantically identical, cosmetically
  different) artifact vs. Klai's currently-deployed `search.cjs`.
- [`patches-source/createStreamServices.ts.patch`](./patches-source/createStreamServices.ts.patch) —
  same treatment for `danny-avila/LibreChat` `packages/api/src/stream/createStreamServices.ts`,
  produced as a byproduct of investigating open question 2 and the
  inert-patch finding. Not requested by the task's explicit checklist but
  included since it was already verified end-to-end and directly
  substantiates section 4's finding with a concrete diff artifact.

Both patch files are unified `git diff` output, generated from a pristine
`git checkout` of the respective pinned tag, and independently re-verified
via a fresh `git apply --3way` from a clean checkout (not just "it looked
right when I wrote it").
