# Klai Tenant-Isolation Review System

Geautomatiseerd review-systeem voor tenant-isolation patterns in klai. Codifies de 15 patterns uit de [audit-tenant-isolation-2026-05-05](../../../../reports/audit-tenant-isolation-2026-05-05/standards.md) audit als reusable diff-time checks.

## Componenten

```
.claude/
├── skills/klai/tenant-isolation-checks/SKILL.md   # 15 checks codified
├── agents/klai/tenant-review.md                    # Diff reviewer agent
├── commands/klai/tenant-review.md                  # /klai:tenant-review slash command
└── hooks/klai/session-end-tenant-review.sh         # Stop-hook suggestion (suggestion-only)

.github/workflows/
└── tenant-isolation-review.yml                     # PR-time CI checks (ast-grep based)
```

## Wanneer wordt het gebruikt

| Trigger | Wat draait | Output |
|---|---|---|
| `/klai:tenant-review` (handmatig) | `klai-tenant-review` agent op `git diff main` | Structured review (HARD/SOFT findings) |
| `/klai:tenant-review origin/main` | Idem, maar pre-push (vs origin) | Idem |
| Stop-hook op session-end | Detecteert tenant-relevante files, suggereert command | One-line nudge in terminal |
| GitHub PR op tenant-paths | `tenant-isolation-review` workflow | CI annotations als warnings |
| Automatisch in PR (LLM, opt-in) | `klai-tenant-review` agent headless | PR comment |

## De 15 checks (zie SKILL.md voor details)

| # | Check | Verdict | Standards § |
|---|---|---|---|
| 1 | Postgres RLS coverage op nieuwe modellen | HARD | §1, §2 |
| 2 | Sessie-helper discipline (`set_tenant`, etc.) | HARD | §3, §4 |
| 3 | Cat-A WITH CHECK explicit | HARD | §2 |
| 4 | `_require_<X>_secret` validators | HARD | §5 |
| 5 | Webhook handler composite | HARD | §6, §15 |
| 6 | Identity-assertion op internal endpoints | HARD | §7 |
| 7 | Qdrant filter-key discipline | HARD | §11 |
| 8 | FalkorDB / Graphiti per-org | HARD | §12 |
| 9 | Garage S3 access | SOFT (HARD na SPEC-TI-009) | §13 |
| 10 | Redis tenant-prefixing | HARD | §14 |
| 11 | Multi-org user resolution | HARD | §10 |
| 12 | Platform-admin gating | HARD | §16 |
| 13 | Constant-time secret compare | HARD | §15 |
| 14 | post_deploy SQL operator-step | SOFT | §8 |
| 15 | Auto-migrate via entrypoint.sh | HARD | §9 |

## Hoe te gebruiken

### Manueel — voor `git push`

```
/klai:tenant-review
```

Output: lijst met HARD findings (block merge) en SOFT findings (review). Elke finding heeft `file:line` anchor + suggestie.

### Pre-PR check

```
/klai:tenant-review origin/main
```

Vergelijkt tegen de remote main — handig voor laatste sanity check vóór `gh pr create`.

### Stop-hook activeren (optioneel)

De hook is suggestion-only — print een nudge bij session-end als je tenant-files hebt aangeraakt. Activeren door toe te voegen aan `.claude/settings.json` of `.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/klai/session-end-tenant-review.sh"
          }
        ]
      }
    ]
  }
}
```

Disable per sessie: `export KLAI_TENANT_REVIEW_SUGGESTION=0`.

### GitHub Actions

`.github/workflows/tenant-isolation-review.yml` triggert automatisch op PRs die tenant-paths raken. Vandaag:
- AST-grep checks (Cat-A WITH CHECK, constant-time compare, webhook composite, validator presence, Qdrant filter-key)
- LLM review is **opt-in** (gated `if: false`) — flip aan met `ANTHROPIC_API_KEY` in repo secrets

## Verschil met `klai-security-audit`

| Aspect | `/klai:tenant-review` (this) | `klai-security-audit` |
|---|---|---|
| Scope | Diff (changed files) | Hele monorepo |
| Doel | Catch regressies vóór merge | Periodieke full-audit |
| Tijd | 1-3 min per diff | 30-60 min |
| Trigger | Per-PR / per-session | Quarterly / pre-livegang |
| Patterns | 15 specifieke (audit 2026-05-05) | 6 review-lenses (klai topology) |

**Workflow:** klai-tenant-review = continu, klai-security-audit = periodiek.

## Toekomstige uitbreiding

Patterns die nog NIET zijn gecodificeerd (kandidaten voor toekomstige checks):
- SSRF-patronen op user-URL-fetching services (covered door SPEC-SEC-SSRF-001 ast-grep rules — kunnen worden samengevoegd)
- Frontend tenant-isolation (cookies, localStorage scoping) — out-of-scope vandaag
- E2E tenant-grens-tests (Playwright per-tenant fixtures)

Update de SKILL.md als nieuwe patterns ontstaan via `/klai:retro` of een nieuwe audit.

## Onderhoudsritueel

Na elke security-audit (~quarterly):
1. Update `standards.md` met nieuwe patterns
2. Voeg checks toe aan `SKILL.md`
3. Update `tenant-review.md` agent als de output-shape verandert
4. Voeg ast-grep rules toe aan workflow als de pattern detecteerbaar is statisch

## Onderhoud per pattern-fix

Wanneer een audit-finding implementeer wordt:
- Update `SKILL.md` om naar de gemerge SPEC te verwijzen
- Wanneer een pattern volledig is uitgerold: schuif van SOFT naar HARD
