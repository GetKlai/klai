# Klai documentation

What is in this directory and where to start. Documentation of a single service lives next to its code (`klai-portal/docs/`, `klai-docs/docs/`, the `README.md` of each library under `klai-libs/`); self-hosting is described in `deploy/README.md`.

| Directory | Contents |
|-----------|----------|
| [`architecture/`](architecture/README.md) | How the platform works today, how it came to be (the evolution documents) and what is designed but not built. Start here |
| [`runbooks/`](runbooks/) | How to operate the system: alerts and their triage, rebuilds and patches of a knowledge base, rollouts and rollback, login and provisioning problems, local development |
| [`retros/`](retros/) | Write-ups of past incidents: what happened and what changed afterwards |
| [`specs/`](specs/) | Specifications of features, with their status. A spec records what was decided when it was written; the architecture documents say what runs |
| [`research/`](research/README.md) | Research behind the design decisions: knowledge system fundamentals, retrieval, the graph, assertion modes, evaluation, model selection |
| [`privacy/`](privacy/) | Telemetry modes and the telemetry addendum to the data processing agreement |
| [`setup/`](setup/) | Developer tooling: code intelligence and MCP servers |
| [`testing/`](testing/) | The plan for the test suite |
| [`contributing/`](contributing/infrastructure-boundary.md) | What belongs in this public repository and what stays in private infrastructure |
| [`reference/`](reference/) | Reference measurements kept for later comparison |
| [`audit-ingest-pipeline-2026-05-06/`](audit-ingest-pipeline-2026-05-06/findings.md) | The ingest pipeline audit of May 2026, kept as a dated record |
| [`gtm/`](gtm/README.md) | The go-to-market agents and how they are set up |

Audit reports on tenant isolation and dependencies are in [`reports/`](../reports/) at the repository root.
