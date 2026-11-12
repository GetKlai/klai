# Runbook — Bumping `vexaai/transcription-service`

> Commands are given as they run on gpu-01, from the GPU deploy directory (`<gpu-deploy-dir>`, the one holding its `docker-compose.yml`). See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands).

The Vexa transcription-service is the one Vexa image we cannot pull from
Docker Hub. It ships only as upstream source (CUDA build, multi-GB image),
so every bump requires a manual `docker build` on `gpu-01`.

This runbook is the canonical procedure. Diverge at your own risk.

## When to bump

- Upstream Vexa-ai/vexa ships a transcription-relevant fix (admission
  control, hallucination filter, VAD, faster-whisper version, CUDA
  compatibility).
- A regression is observed in the workers (CUDA OOM, transcript drift,
  latency spike).

Do **not** bump in lock-step with the core stack (`admin-api`,
`api-gateway`, `meeting-api`, `runtime-api`, `vexa-bot`) just for tidiness.
The HTTP contract between meeting-api and the transcription LB is stable
across upstream minor versions; mixed-version is fine.

## Pre-flight (no changes yet)

```bash
# 1. Verify upstream has the tag with the service.
gh api repos/Vexa-ai/vexa/contents/services/transcription-service?ref=v0.10.6 --jq '.[] | .name'
# Expect: Dockerfile, main.py, nginx.conf, requirements.txt, …

# 2. On gpu-01: verify it has headroom.
df -h / | tail -1                                   # need >20G free
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
docker images vexaai/transcription-service --format '{{.Tag}}\t{{.Size}}'

# 3. Confirm the rollback image is still on disk (do NOT prune it).
```

## Build (gpu-01, leaves running workers untouched)

```bash
# On gpu-01:
TAG="0.10.6-local-$(date +%y%m%d-%H%M)"   # local convention; check-image-pullable.sh accepts this

cd <gpu-deploy-dir>/..                    # the parent of the GPU deploy directory
[ -d vexa-src ] && rm -rf vexa-src
git clone --depth 1 --branch v0.10.6 https://github.com/Vexa-ai/vexa.git vexa-src
cd vexa-src/services/transcription-service
docker build -t vexaai/transcription-service:$TAG .
docker images vexaai/transcription-service
```

The build is ~10–15 min on gpu-01 (CUDA base layer caches across runs).
Old image tag stays on disk for rollback.

## Recreate workers (canary, then full)

`docker-compose.yml` in the GPU deploy directory (`<gpu-deploy-dir>`) is hand-edited on gpu-01 (no CI sync).
Repo file at `deploy/docker-compose.gpu.yml` is the audit copy.

```bash
# On gpu-01. Canary: worker-1 first.
cd <gpu-deploy-dir>
sed -i.bak "s|image: vexaai/transcription-service:[A-Za-z0-9._-]\+|image: vexaai/transcription-service:$TAG|" docker-compose.yml
# Verify only worker-1 changed (sed targets first match if you scope it).
# If both workers should canary independently, edit by line number instead.
docker compose up -d transcription-worker-1

# Observe for 5–10 min during a real meeting.
# Healthcheck:
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep transcription

# If healthy: bring worker-2 over with the same sed + up -d.
```

## Repo alignment

After the live image runs, sync the repo:

1. Update `deploy/docker-compose.gpu.yml` — both `transcription-worker-{1,2}`
   image refs to the new tag.
2. Update the prose in `deploy/VERSIONS.md` only if the build recipe changed; the tag lives in compose and the document states no version.
3. Run `sh deploy/check-image-pullable.sh` — must say `OK: … N locally-built ref(s) accepted.`
4. Run `sh deploy/check-image-tags.sh` — same.
5. Open PR, merge.

## Rollback

```bash
# On gpu-01:
cd <gpu-deploy-dir>
mv docker-compose.yml.bak docker-compose.yml   # if .bak still exists
# OR sed it back to the previous tag, which is still on disk.
docker compose up -d transcription-worker-1 transcription-worker-2
```

Old images remain on disk (do not `docker image prune` until the new
build has soaked for at least a week of real meetings).

## What can NOT go wrong this way

The forcing function `deploy/check-image-pullable.sh` runs in
pre-commit and in the `deploy-compose.yml` workflow. If a future PR
bumps a `vexaai/*` ref to a tag that does not exist on Docker Hub
**and** does not match the locally-built tag pattern
(`<semver>-local-YYMMDD-HHMM` or legacy `<semver>-YYMMDD-HHMM`), the
commit is rejected. PR #269 would have failed this check.

## See also

- `.claude/rules/klai/pitfalls/process-rules.md` — `verify-image-pullable-before-pin` pitfall.
- `deploy/VERSIONS.md` — why each image is held where it is (no versions; compose is the pin).
- SPEC-VEXA-003 deploy-notes — original local-build context.
