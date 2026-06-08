# Contributing to Klai

Klai is open source. You can contribute to product code, local development,
self-hosting templates, tests, documentation, and public build workflows without
access to Klai's private production infrastructure.

## What You Can Work On

- `klai-portal/` — portal frontend and backend
- `klai-scribe/` — transcription product code and Whisper image build inputs
- `klai-retrieval-api/`, `knowledge-ingest`, and related libraries
- `klai-libs/` — shared Python packages
- `deploy/` — self-hosting templates and public-safe service configuration
- `docs/` — architecture, testing, privacy, self-hosting, and contributor docs
- `.github/workflows/` — public build, lint, test, and scan workflows

## Production Infrastructure Boundary

Klai's live production server inventory, SSH procedures, host addresses, tunnel
topology, DNS targets, SOPS material, and operator runbooks are private. They
live in the private `GetKlai/klai-infra` repository and are intentionally not
required for open-source contribution.

Do not add live host addresses, direct SSH commands, key paths, unlock
procedures, production DNS targets, or private operator runbooks to this public
repository.

For the detailed boundary, see
[docs/contributing/infrastructure-boundary.md](docs/contributing/infrastructure-boundary.md).

## Build vs Deploy

The public repository builds and tests application artifacts. Klai's production
deployment is run from private infrastructure automation. This split keeps the
open-source project usable while avoiding publication of live operational
details.

Self-hosters can use the public `deploy/` templates as a starting point and
provide their own host inventory, secrets, and deployment procedures.

## Before Opening a Pull Request

- Run the focused tests for the area you changed.
- Keep production-specific details out of code and docs.
- Prefer examples with placeholder domains, hostnames, and secrets.
- Document public behavior and interfaces, not Klai's private production
  topology.
