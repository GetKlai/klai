---
description: Create and run E2E tests with Chrome, Playwright, or Agent Browser
argument-hint: "[--record] [--url URL] [--journey NAME]"
allowed-tools: Skill
---

Before any browser navigation:
- Local portal checks: run `scripts/local-dev-status.sh --mode local --strict`.
- Production E2E: run `scripts/local-dev-status.sh --mode prod-e2e`.
- If a local check redirects to `my.getklai.com/login`, stop and diagnose instead of continuing.

Use Skill("moai") with arguments: e2e $ARGUMENTS
