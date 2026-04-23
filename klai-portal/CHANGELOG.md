# Changelog

All notable changes to klai-portal are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased]

### Added

- SPEC-PORTAL-UNIFY-KB-001: Knowledge is now the single KB surface. Core plan
  includes knowledge with per-user limits (5 KBs × 20 documents). Complete plan
  unlocks connectors, members, taxonomy, gaps, advanced.
- Backend capability enforcement on connectors/members/taxonomy/gaps routes
  (`require_capability` dependency).
- Concurrent-safe personal-KB quota via `pg_advisory_xact_lock` (K2 fix).

### Changed

- `core`, `professional`, and `complete` plans now all include the `knowledge`
  product. Access is gated by per-plan `KBLimits` instead of product presence.
- Billing page labels: "Chat" / "Chat + Scribe" / "Chat + Scribe + Knowledge".
- `/app/focus/*` routes now redirect to `/app/knowledge` (all sub-paths included).

### Removed

- The entire `/app/focus/*` route tree. Notebooks have been collapsed into the
  personal KB in `/app/knowledge`.
- `research-api` service (klai-focus). Docker service, volume mounts, health
  checks, proxy handler, SOPS vars (`RESEARCH_API_ZITADEL_AUDIENCE`,
  `KUMA_TOKEN_RESEARCH_API`) all removed. `klai-focus/` submodule retained for
  git history but marked FROZEN.
