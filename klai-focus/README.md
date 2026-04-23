# FROZEN — replaced by Knowledge per SPEC-PORTAL-UNIFY-KB-001

This directory contains the former `research-api` service (Focus module). It is
**no longer built, deployed, or maintained**.

## What happened

Focus and Knowledge did the same thing (upload + Q&A with citations). They were
collapsed into a single surface in SPEC-PORTAL-UNIFY-KB-001:

- `/app/focus/*` routes redirect to `/app/knowledge`.
- `research-api` was removed from `deploy/docker-compose.yml`.
- `knowledge-ingest` is the sole KB backend going forward.
- Focus data is not migrated — the volume `/opt/klai/research-uploads` can be
  cleaned up manually after confirming no active user data remains.

## History

Kept in the git tree for historical reference only. Do not resurrect.

See `.moai/specs/SPEC-PORTAL-UNIFY-KB-001/spec.md` for the full decommission
rationale (Design Decision D6).
