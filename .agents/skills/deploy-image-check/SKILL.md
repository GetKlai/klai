---
name: deploy-image-check
description: Validate vexaai/* image tags in deploy compose files before committing or deploying. Use when changing deploy/docker-compose*.yml image references, bumping Vexa versions, or debugging a "manifest not pullable" CI failure.
---

# Deploy image check (pinned + pullable)

Two scripts guard every `vexaai/<svc>:<tag>` reference in `deploy/docker-compose.yml` and `deploy/docker-compose.gpu.yml`. Run both before committing compose changes:

```sh
sh deploy/check-image-tags.sh        # tag FORM: pinned only
sh deploy/check-image-pullable.sh    # tag EXISTENCE: manifest on registry
```

They are wired into `.githooks/pre-commit` (local half) and the `deploy-compose.yml` CI workflow (CI half, runs before anything syncs to core-01).

## Rules the scripts enforce

**check-image-tags.sh** — only three pinned tag forms are accepted:
1. plain semver `X.Y.Z` (upstream publishes to Docker Hub since v0.10.4)
2. `X.Y.Z-local-YYMMDD-HHMM` (built on-host, preferred since 2026-05-03)
3. `X.Y.Z-YYMMDD-HHMM` (legacy local convention, kept for rollback)

Mutable tags (`latest`, `dev`, `staging`) and `-pending` placeholders fail the commit.

**check-image-pullable.sh** — every ref must either have a manifest on the public registry (`docker manifest inspect`) or match a locally-built convention. Origin story: PR #269 shipped `vexaai/transcription-service:0.10.6`, a tag that does not exist upstream (that service is built locally on gpu-01) — it passed review and only broke at `docker compose pull` time on the server.

## The unauthenticated requirement

The pullability check MUST run without a local registry login. `docker manifest inspect` is anonymous-friendly for public repos — that is the point: it verifies what the *servers* can pull anonymously. A local `docker login` can make private or team-scoped images resolve on your machine while the server's anonymous pull still fails, silently defeating the check. If you are logged in, log out (or use a clean environment) before trusting a green result.

## When a check fails

- Tag typo / never-published upstream tag → fix to a tag that exists on Docker Hub.
- Genuinely locally-built image → rename the tag to `X.Y.Z-local-YYMMDD-HHMM`.
- Do not bypass by adding the ref to an ignore list — neither script has one, by design.
