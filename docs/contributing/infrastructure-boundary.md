# Infrastructure Boundary

Klai's public repository is intended to be useful for contributors and
self-hosters without exposing Klai's live production operations.

## Public Repository

The public `GetKlai/klai` repository contains:

- product source code
- tests and public build workflows
- self-hosting templates
- public-safe architecture documentation
- examples using placeholder hosts, domains, credentials, and tokens

Public documentation should explain product behavior, service contracts, local
development, and self-hosting patterns. It should not describe Klai's private
production topology.

## Private Infrastructure

The private `GetKlai/klai-infra` repository contains:

- live server inventory
- SSH aliases, jump-host procedures, and operator access
- DNS/provider operations for Klai production
- SOPS-encrypted secrets and secret-management runbooks
- production deploy workflows and verification runbooks
- live GPU/transcription operator procedures

## Private Business And Compliance

The private `GetKlai/klai-private` repository contains internal GTM,
compliance, research, and business material that is not required for
open-source development.

## Writing Public Docs Safely

Use placeholders:

```text
example.com
203.0.113.10
deploy@example-host
/path/to/private/key
```

Do not publish:

- live host addresses or DNS targets
- direct SSH commands to Klai production servers
- private key paths or jump-host topology
- unlock procedures or server inventory
- production-only secrets or token names that identify a live credential

When a public document needs to mention production deployment, describe the
contract instead: public artifacts are built and tested here; Klai production
deployment is operated from private infrastructure automation.

## How Public Runbooks Write Commands

A runbook gives every command as it runs on the host that executes it, so an
operator pastes it into a shell there. How an operator reaches that host (login
command, key, jump host, tunnel) is in the private `klai-infra` runbooks, and a
public runbook does not repeat it.

- A step that runs on a server says on which role it runs ("on core-01",
  "on gpu-01"). The role names are the only host names a runbook uses.
- Paths on a server are relative to the deploy directory, the directory on that
  host that holds the `docker-compose.yml`. A runbook that needs the directory
  by name writes `<deploy-dir>` (`<gpu-deploy-dir>` for gpu-01) and starts the
  block with `cd <deploy-dir>`.
- A command that is split between your machine and the host says so in words
  ("on your machine", "on core-01") and shows each part on its own.
- Self-hosting guides address the reader's own server with the placeholders
  above (`deploy@example-host`).

