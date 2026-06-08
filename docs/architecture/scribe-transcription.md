# Scribe And Transcription Architecture

Scribe is Klai's transcription product surface. It accepts meeting and audio
workflows through product APIs and delegates speech-to-text work to a Whisper
service.

This document is public-safe. It describes service responsibilities and
interfaces, not Klai's live production hosts or operator procedures.

## Components

| Component | Responsibility |
|-----------|----------------|
| `klai-scribe/whisper-server` | Containerized Whisper runtime used for speech-to-text inference. |
| `scribe-api` | Product API for transcription workflows, status, and integration with the portal. |
| `klai-portal` | User-facing transcription controls and tenant/product management. |
| public build workflows | Build, scan, and publish container images. |
| private infra workflows | Deploy Klai production instances and verify live service health. |

## Contributor Model

Contributors can work on:

- Whisper image code and dependency updates
- Scribe API behavior and tests
- Portal transcription UI and product flows
- public image build and security scan workflows
- local or self-hosted deployment templates

Contributors do not need access to Klai production servers. Production host
inventory, SSH procedures, DNS targets, and live deployment commands are kept in
the private infra repository.

## Build And Deploy Split

The public repository owns build-time concerns:

- source code
- Docker image definitions
- tests
- vulnerability scans
- GHCR image publication

Production deployment is a private operations concern. Klai's production deploy
automation consumes public image tags and runs from `GetKlai/klai-infra`.

Self-hosters should provide their own host inventory, secret management, and
deployment automation. The public `deploy/` directory is a template, not a copy
of Klai's production infrastructure.

## Public Interface Expectations

The product contract is:

- transcription jobs can be created and tracked through Scribe/portal APIs
- the Whisper runtime is replaceable by any compatible self-hosted inference
  backend
- deployment topology is environment-specific

Public docs should focus on these contracts. Live production topology belongs in
private infrastructure docs.
