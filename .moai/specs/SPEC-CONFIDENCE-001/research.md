# SPEC-CONFIDENCE-001 Research: Vexa Conductor Analysis

**Date:** 2026-04-02
**Source:** https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime

## Vexa Conductor — Samenvatting

Vexa bouwde een AI development framework ("Conductor") dat Claude Code autonoom laat werken aan een meeting-transcriptieplatform met 15 features en 12 services. Na $50 en 48 uur experimenteren documenteerden ze hun bevindingen in LEARNINGS.md.

### Kernarchitectuur

```
User beschrijft missie → Conductor maakt mission.md + bouwt prompt
    → claude --worktree {name} (dev agent werkt)
        → Stop hook blokkeert vroeg stoppen
        → check-completion.py verifieert DoD
    → claude --worktree {name} (evaluator beoordeelt)
    → User verifieert
```

### Kerncomponenten

| Component | Wat het doet | Vexa bron |
|---|---|---|
| Mission files | 5-regel taakdefinitie (Focus, Problem, Target, Stop-when, Constraint) | `conductor/missions/*.md` |
| Stop Hook | Bash script dat agent blokkeert van stoppen zonder bewijs | `conductor/hooks/confidence-check.sh` |
| Completion checker | Python script dat DoD verifieert tegen state.json | `conductor/check-completion.py` |
| Confidence framework | Bayesiaans model met gotcha memory en adversarial checks | `.claude/confidence-framework.md` |
| Feature READMEs | Design (aspirationeel) + State (bewezen) split | `features/*/README.md` |
| Evaluator agent | Skeptische reviewer die claims verifieert | `.claude/agents/evaluator.md` |
| Gotcha catalog | Machine-leesbare failure patterns met severity en decay | In CLAUDE.md (G1-G11) |

## Vexa's Kernlessen (uit LEARNINGS.md)

### Overgenomen voor Klai

| Les | Vexa bewijs | Klai toepassing |
|---|---|---|
| Mechanische enforcement > prompt instructies | Agent negeerde planning-fase "read-only" regel, backgroundde processen, werkte op main ipv branch | Stop hook die confidence-rapportage afdwingt |
| "Code looks correct" = 0 confidence | Evaluator ving 4+ bugs die dev agent niet zag ondanks "looks correct" claims | Evidence scoring tabel in confidence.md |
| Adversarial check bij 80% verlaagt overconfidence 15pp | Kaddour et al. 2026; Vexa evaluator ving stale scores, auth bugs, false completion claims | Stop hook blokkeert bij 80+ zonder adversarial language |
| Diagnose voor fixing — geen flailing | Agent probeerde 4 fixes (allowedDevOrigins, restart, cookies, kill) zonder root cause; allemaal mislukt | Gotcha G2 + process rule `diagnose-before-fixing` |
| System health verificatie voor feature tests | 8/8 Playwright tests passed, dashboard unreachable door agent's eigen curl bombardment | Stop hook health check bij code changes |
| Gotcha memory is belangrijkste memory | Severity + confirmation tracking maakt systeem slimmer over tijd | gotchas.md met structured entries |
| Vertel WHAT niet HOW | Micro-management regels ("poll elke 10s") killden werkende processen | Geen prescriptieve timing/polling regels toevoegen |

### Niet overgenomen voor Klai

| Les | Vexa aanpak | Waarom niet |
|---|---|---|
| Bayesiaans confidence model (log-odds sigmoid) | Wiskundig model met evidence_strength per signaal | In praktijk checkt Vexa alleen "is er een getal + is er bewijs" — de wiskunde wordt niet gerund. Onze binaire Stop hook is effectiever. |
| Conductor als orchestrator | Mission files → worktree → dev+validator team → evaluator | Wij hebben MoAI met SPEC-workflow. Zelfde concept, andere implementatie. |
| README als source of truth (Design/State split) | Agent moet README lezen voor context en State bijwerken na wijzigingen | Vexa's eigen conclusie: "agent leest code, niet README. State wordt nooit bijgewerkt." Ons SPEC-systeem werkt beter. |
| Multi-agent teams met coordinator | Dev + validator + coordinator | Vexa's conclusie: "coordinator deed niets nuttigs, 15+ ronden shutdown-onderhandeling." |
| Weighted DoD met ceiling mechanic | Risk-gewogen items, kritiek pad kan totaalscore cappen | Goed concept maar te complex voor huidige schaal. Kan later als fase 2. |
| Evaluator als apart agent | Skeptische reviewer na elke delivery | Onze manager-quality agent doet dit al. Geen apart agent nodig. |
| state.json met score tracking | Centraal bestand dat feature scores en iteratie-geschiedenis bijhoudt | TodoWrite is al onze completion tracker. |

## Vexa Gotchas — Bron voor Klai

Vexa documenteerde 11 gotchas (G1-G11). Hieronder de relevante voor Klai:

| Vexa ID | Patroon | Klai-relevantie |
|---|---|---|
| G1 | Test the system, not just the feature — agent's testing broke nginx | Hoog: wij doen ook API-level testing zonder system health check |
| G2 | Don't flail — diagnose before fixing | Hoog: we zien dit patroon regelmatig |
| G3 | CLAUDE.md changes don't reach running sessions | Medium: known limitation, al gedocumenteerd |
| G4 | Instructions alone don't change behavior | Hoog: kernargument voor Stop hook |
| G5 | Relative paths in hooks break from subdirectories | Medium: relevant voor hook implementatie |
| G6 | Search ALL case variants when renaming | Hoog: kebab/snake/camelCase/SCREAMING_SNAKE |
| G7 | Convention changes need full-codebase consumer search | Hoog: blast radius van defaults is onbegrensd |
| G8 | Signals need specificity, not just presence | Medium: false positive prevention |
| G9 | Never use :dev/:latest tags for development | Laag: wij taggen al met SHA/timestamps |
| G10 | All env vars come from .env — never hardcode | Medium: al in onze infra patterns |
| G11 | Confidence must reflect the critical path, not easy tests | Hoog: 10/15 passing != 75% als de 5 ontbrekende kritiek zijn |

## Academische Referenties (via Vexa's research)

| Paper | Bevinding | Relevantie |
|---|---|---|
| Kaddour et al., Feb 2026 | Agents 5.5x vaker confident-fout dan onzeker-over-iets-goeds | Kernargument voor evidence-only confidence |
| Dunning-Kruger in LLMs, March 2026 | Slechtst presterende modellen hebben hoogste confidence | Versterkt: self-reported confidence is onbetrouwbaar |
| Adversarial framing research | Herformulering als "welke bugs?" ipv "is dit correct?" verlaagt overconfidence ~15pp | Direct overgenomen in adversarial check |
| Reflexion (Shinn et al., NeurIPS 2023) | Verbale zelfreflectie in episodisch geheugen verbetert prestatie significant | Ondersteunt gotcha-systeem als persistent geheugen |

## Bronbestanden op GitHub

| Bestand | URL |
|---|---|
| LEARNINGS.md | `conductor/LEARNINGS.md` |
| Confidence Framework | `.claude/confidence-framework.md` |
| CLAUDE.md (gotchas) | `.claude/CLAUDE.md` |
| Evaluator agent | `.claude/agents/evaluator.md` |
| Stop hook (confidence) | `conductor/hooks/confidence-check.sh` |
| Stop hook (mission) | `conductor/hooks/mission-check.sh` |
| Completion checker | `conductor/check-completion.py` |
| Settings (hook config) | `.claude/settings.json` |
| Feature README template | `features/.readme-template.md` |

Alle bestanden op branch `feature/agentic-runtime` van `Vexa-ai/vexa`.
