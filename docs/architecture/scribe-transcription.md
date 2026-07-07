# Scribe And Transcription Architecture

Scribe is Klai's transcription product surface. It accepts meeting and audio
workflows through product APIs and delegates speech-to-text work to a
configurable speech-to-text backend.

This document is public-safe. It describes service responsibilities and
interfaces, not Klai's live production hosts or operator procedures.

## Components

| Component | Responsibility |
|-----------|----------------|
| `klai-scribe/whisper-server` | Public reference image for the self-hosted Whisper-compatible speech-to-text backend. |
| Transcription backend | OpenAI-compatible speech-to-text endpoint selected by `WHISPER_SERVER_URL` / `TRANSCRIPTION_SERVICE_URL`. Klai production operates this from private infrastructure; self-hosters can run a compatible backend behind an allowed Scribe hostname. |
| `scribe-api` | Product API for transcription workflows, status, and integration with the portal. |
| Vexa meeting stack | Meeting bot lifecycle and live transcript collection. |
| `klai-portal` | User-facing transcription controls and tenant/product management. |
| public build workflows | Build, scan, and publish container images. |
| private infra workflows | Deploy Klai production instances and verify live service health. |

## Contributor Model

Contributors can work on:

- Scribe API behavior and tests
- Whisper reference image code and dependency updates
- Portal transcription UI and product flows
- public image build and security scan workflows
- local or self-hosted deployment templates

Contributors do not need access to Klai production servers. Production host
inventory, SSH procedures, DNS targets, and live deployment commands are kept in
the private infra repository.

## Backend Contract

The public product contract is intentionally backend-shaped instead of
host-shaped:

- `scribe-api` reads `WHISPER_SERVER_URL` as the base URL for speech-to-text.
- `WHISPER_SERVER_URL` is validated at `scribe-api` startup against its SSRF
  allowlist. Self-hosters should expose the backend through an allowed hostname
  such as `whisper`, `whisper-server`, localhost, the documented bridge gateway,
  or intentionally extend that allowlist with tests.
- Upload transcription calls `POST {WHISPER_SERVER_URL}/v1/audio/transcriptions`.
- Meeting transcription uses the Vexa `TRANSCRIPTION_SERVICE_URL`, which must
  point at the same OpenAI-compatible endpoint path.
- Requests include the OpenAI-compatible `model` form field.
- Scribe upload traffic sends `transcription_tier=deferred` so live meeting
  traffic can take priority when the backend supports admission tiers.
- A busy backend should return `503` with `Retry-After`; `scribe-api` retries
  this boundedly and surfaces a temporary-unavailable error if capacity stays
  exhausted.

The endpoint can be backed by Whisper, faster-whisper, Vexa
`transcription-service`, or another compatible implementation that satisfies the
Scribe URL contract. The public repo does not require a specific production
host, SSH tunnel, or GPU layout.

## Build And Deploy Split

The public repository owns build-time concerns:

- source code
- Docker image definitions
- the public Whisper reference image
- tests
- vulnerability scans
- GHCR image publication

Production deployment is a private operations concern. Klai's production deploy
automation consumes public image tags and runs from `GetKlai/klai-infra`.
Live GPU compose files, tunnel procedures, Uptime Kuma push scripts, and
transcription-service bump runbooks belong there, not in this public repository.

Self-hosters should provide their own host inventory, secret management, and
deployment automation. The public `deploy/` directory is a template, not a copy
of Klai's production infrastructure.

## Public Interface Expectations

The product contract is:

- transcription jobs can be created and tracked through Scribe/portal APIs
- the transcription runtime is replaceable by a compatible self-hosted inference
  backend that satisfies the configured URL and allowlist contract
- deployment topology is environment-specific

Public docs should focus on these contracts. Live production topology belongs in
private infrastructure docs.
