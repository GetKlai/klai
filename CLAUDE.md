# Klai: Shared Claude Configuration

These are the shared base instructions for all Klai projects.
Project-specific instructions are in each project's own CLAUDE.md.

## About Klai

Klai builds AI-powered go-to-market tools for B2B sales and marketing teams.
Primary language: Dutch. Secondary language: English.

## Infrastructure

- **Server:** Hetzner CX42 — `ssh root@65.109.237.64`
- **Deployment:** Coolify at `http://65.109.237.64:8000`
- **GitHub:** `git@github.com:GetKlai/` (organisation)
- **Domain:** getklai.com (registrar: Registrar.eu, DNS: Cloud86)

## Process rules

@docs/pitfalls/process.md

## Working principles

- Minimal changes: only what was asked
- No em dashes (--) in content or code
- No display:none/block for content switching
- All code, comments, scripts, and documentation must be in English (UI content for Dutch users is the exception)
- Never output private keys, secrets, or credentials in the conversation — instruct the user to read the file themselves instead
- SPEC-First: never start implementation without a SPEC document — always `/plan` first, then `/run`, then `/sync`
- Use `/clear` between SPEC phases (plan → run → sync) to prevent context contamination across phases
- Ask before high-impact: bulk DB operations, destructive scripts, >25 external API calls — show what will be affected and wait for explicit confirmation

## Agent configuration

This repo contains three layers of agents:

- `agents/moai/` — MoAI-ADK agents (upstream, do not edit manually)
- `agents/gtm/` — GTM content agents (Klai-owned, adapted for getklai.com)
- `agents/klai/` — Klai-built agents

Writing style for all content: see `rules/gtm/klai-brand-voice.md`
