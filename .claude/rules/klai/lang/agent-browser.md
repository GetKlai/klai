---
paths:
  - "**"
---
# Agent Browser

> AI-driven browser CLI by vercel-labs. Naast (niet vervang) Playwright MCP.
> Source: https://github.com/vercel-labs/agent-browser
> Installed: `npm i -g agent-browser && agent-browser install`

## Tool selection — Agent Browser vs Playwright MCP

| Use case | Tool | Reden |
|---|---|---|
| Verify-changes-landed (bekende selectors, deterministisch) | Playwright MCP | Stable, snapshot-driven asserts, headed visible browser |
| Smoke test na deploy (intent: "klop service aan, niets stuk?") | **Agent Browser** | AI-navigatie tolereert UI-drift, geen selector-onderhoud |
| Onboarding/signup regression (intent-stable, UI-volatile) | **Agent Browser** | Idem |
| Audit-style runs (a11y, copy-consistency, design checks) | **Agent Browser** | Past bij exploratory scoring door evaluator-active |
| One-off CSS/visual check zonder login | Playwright MCP via incognito-tab | Eén MCP-server, ingelogd via storage-state — open een nieuwe tab als je een schone context wilt |
| Authenticated portal regression met vaste selectors | Playwright MCP | Storage-state via `~/.claude/mcp-storageState.json` |

Default: voor alles met "verify dat X werkt" → Playwright. Voor alles met "explore of er iets stuk is" → Agent Browser.

## Parallel sessions [HARD]

Elke Claude sessie MOET een unieke `--session` naam gebruiken. Anders leakt cookies/localStorage tussen sessies (issue [#1068](https://github.com/vercel-labs/agent-browser/issues/1068)).

```bash
# CORRECT — per Claude sessie eigen browser-instance
SESSION="klai-${CLAUDE_SESSION_ID:-$(date +%s)}"
npx agent-browser --session "$SESSION" open https://app.getklai.com
npx agent-browser --session "$SESSION" snapshot -i -c
npx agent-browser --session "$SESSION" close

# FOUT — leakt cookies tussen parallelle agents
npx agent-browser --auto-connect snapshot
```

Resource cost: elke unieke session = eigen Chromium-proces (~100MB). **Practical limit: max 3 parallelle sessies.** Bij meer: serialize of accepteer geheugendruk.

## Command chaining (zelfde sessie binnen één Bash call)

`$$` of `$RANDOM` expanderen per shell-invocation; gebruik een vaste env var:

```bash
# CORRECT
SESSION="klai-smoke" \
  && npx agent-browser --session "$SESSION" open https://getklai.com \
  && npx agent-browser --session "$SESSION" wait --load networkidle \
  && npx agent-browser --session "$SESSION" snapshot -i -c

# FOUT — elke command wordt nieuwe sessie
npx agent-browser --session "klai-$$" open https://getklai.com
npx agent-browser --session "klai-$$" snapshot   # andere $$, andere sessie!
```

## Auth handoff voor portal flows

Agent Browser kan **niet** de Playwright `~/.claude/mcp-storageState.json` direct laden. Eigen format via `state save` / `--state`.

**One-time setup** (eenmalig, of refresh wanneer sessie verloopt):

```bash
# 1. Open portal headed (zonder --session-name = ephemeral, dan via state save)
agent-browser open https://app.getklai.com
# 2. Log in handmatig in geopende browser
# 3. Save state
agent-browser state save ~/.claude/agent-browser-state.json
chmod 600 ~/.claude/agent-browser-state.json
agent-browser close
```

**Daily use** in scripts/agents:

```bash
SESSION="klai-portal-${CLAUDE_SESSION_ID:-$(date +%s)}"
agent-browser --session "$SESSION" \
              --state ~/.claude/agent-browser-state.json \
              open https://app.getklai.com/dashboard
```

Refresh storage-state om de paar weken (zelfde cadans als Playwright). Als sessies "logged-out" starten: state file is verlopen → opnieuw `state save`.

## Cleanup [HARD]

Net als Playwright: tabs/sessie sluiten ná elke run. Daemon laat anders Chromium-procs hangen.

```bash
agent-browser --session "$SESSION" close          # sluit één sessie
agent-browser close --all                          # sluit alles (gebruik bij stuck procs)
```

Een hook-equivalent voor `playwright-browser-cleanup.sh` is **niet nodig** zolang agents zelf afsluiten — agent-browser sessies zijn isolated, dus een vergeten Chromium-proc blokkeert geen volgende sessie (anders dan Brave's `SingletonLock`).

## Concrete patterns per use case

### 1. Smoke test na deploy

```bash
SESSION="klai-deploy-smoke-${CLAUDE_SESSION_ID:-$(date +%s)}"
agent-browser --session "$SESSION" open https://getklai.com \
  && agent-browser --session "$SESSION" wait --load networkidle \
  && agent-browser --session "$SESSION" snapshot -i -c -d 3 \
  && agent-browser --session "$SESSION" close
```

Pass-criterium: snapshot bevat verwachte top-level navigatie (`PRODUCT`, `BLOG`, `PRICING`, `JOIN KLAI`). Als snapshot leeg of error: deploy issue.

### 2. Onboarding regression (signup tot eerste vraag)

```bash
SESSION="klai-onboarding"
agent-browser --session "$SESSION" open https://getklai.com \
  && agent-browser --session "$SESSION" find role button click --name "JOIN KLAI" \
  && agent-browser --session "$SESSION" wait --load networkidle \
  && agent-browser --session "$SESSION" snapshot -i -c
# AI agent navigeert verder; intent: "kom tot eerste antwoord"
```

### 3. Audit run (a11y / copy / design)

```bash
SESSION="klai-audit"
agent-browser --session "$SESSION" open https://getklai.com \
  && agent-browser --session "$SESSION" snapshot --json > /tmp/snapshot.json
# Parse JSON voor heading-hiërarchie, missing alt-text, button labels
agent-browser --session "$SESSION" eval "
  const buttons = [...document.querySelectorAll('button')];
  return buttons.filter(b => !b.textContent.trim() && !b.getAttribute('aria-label'));
"
```

## Pitfalls

- **agent-browser-no-shared-chrome** — Nooit `--auto-connect` voor parallelle Claude sessies. Cookies leaken (issue #1068). Use `--session "<unique>"`.
- **agent-browser-session-shell-expansion** — `$$` of `$RANDOM` per command-aanroep = nieuwe sessie elke keer. Gebruik een env var die je vóór de chain set.
- **agent-browser-storage-state-mismatch** — Niet de Playwright `~/.claude/mcp-storageState.json` proberen te laden. Eigen file `~/.claude/agent-browser-state.json` aanmaken via `state save`.
- **agent-browser-stale-refs** — `@e1`, `@e2` zijn **fresh per snapshot**. Re-snapshot na elke navigation/click die de DOM wijzigt.

## See also

- `lang/testing.md` — Playwright MCP workflow (canonical voor verify-flows)
- `pitfalls/process-rules.md` — `playwright-mcp-config-cycle` (waarom we Playwright config niet aanraken)
