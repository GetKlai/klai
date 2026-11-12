# Architecture

How the Klai platform works and how it came to be. The documents fall into three kinds, and each fact has one home: a description of what runs says nothing about why, and a history says nothing about how it works today.

## What runs today

Each of these was checked against the code on 12 November 2026 and carries `path:line` anchors. When one of them and the code disagree, the code is right and the document needs a fix in the same change.

| File | Answers |
|------|---------|
| [platform.md](platform.md) | Which services exist and where they run, tenancy, the portal and its API, login and service authentication, model aliases, the other product surfaces, deployment, observability and backup |
| [chat-system.md](chat-system.md) | (Dutch) How one chat turn runs on the help widget, the partner API and the internal chat: every step, every model call, where answering, asking and refusing are decided, conversation state, what is recorded. Read it before changing the chat path |
| [knowledge-ingest-flow.md](knowledge-ingest-flow.md) | Where knowledge comes from (connectors, crawling, uploads, the docs editor, MCP), the ingest pipeline phase by phase, queues and schedules, what is written where, change and delete, operations |
| [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) | The retrieval service: callers and authentication, how a scope becomes filters, the pipeline from query to evidence pack, what is logged, the nightly evaluation, configuration |
| [klai-knowledge-architecture.md](klai-knowledge-architecture.md) | The knowledge model as stored (fields in Postgres and in the vector store), versions and deletion, the stores, taxonomy, the graph layer, the MCP tools, personal and shared knowledge, the docs app |
| [knowledge-gap-to-article.md](knowledge-gap-to-article.md) | (Dutch) How a question the chat could not answer becomes a proposal the owner of a knowledge base decides on: what is built (phase 1) and the design of what is not |
| [support-gap-detection.md](support-gap-detection.md) | Gap detection from support cases: the research behind it, the implementation contract and what of it is built |
| [scribe-transcription.md](scribe-transcription.md) | Scribe and meeting transcription: components, the backend contract, the build and deploy split |

Login, API authentication and roles are described in detail next to the portal code: [auth-flows.md](../../klai-portal/docs/auth-flows.md), [api-auth.md](../../klai-portal/docs/api-auth.md) and [admin-roles.md](../../klai-portal/docs/admin-roles.md).

## How it came to be

| File | Answers |
|------|---------|
| [evolution.md](evolution.md) | (Dutch) The whole system from March 2026 to today: the phases, a chronology per month, the platform decisions with where each stands now, and what no longer exists |
| [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md) | (Dutch) The evolution document of the chat: the chronology, what research says next to what we do, the design, the working agreements, the plan and the measured outcome of every step. Source files cite its sections by number |
| [knowledge-base-ingestion-and-retrieval-plan.md](knowledge-base-ingestion-and-retrieval-plan.md) | (Dutch) The same for the knowledge base: the state of the art, the plan and the dated outcome of every change to ingest, the stores, the graph and retrieval |

Before proposing a change to the chat or to retrieval, look the idea up in the last two: most obvious ideas have been measured, and the documents say with what result.

## What is designed and not built

| File | Answers |
|------|---------|
| [product-gaps-backlog.md](product-gaps-backlog.md) | The target design for Klai Knowledge that the code does not implement, per `GAP-*` id: what the design wants, what the code does today, the status |

## Research and operations

Research that underpins these documents is in [`docs/research/`](../research/README.md). How to operate the system (alerts, rebuilds, rollouts, incidents) is in [`docs/runbooks/`](../runbooks/), and write-ups of past incidents are in [`docs/retros/`](../retros/).

## Scope

This directory holds what is true of the platform as a whole. Documentation of a single service lives next to its code (`klai-portal/docs/`, `klai-docs/docs/`, the `README.md` of a library under `klai-libs/`).
