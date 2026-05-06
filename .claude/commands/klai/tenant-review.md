---
description: Run tenant-isolation review on current diff (vs main) — checks new code against the patterns from audit-tenant-isolation-2026-05-05/standards.md
allowed-tools: Bash, Read, Grep, Glob, Agent
argument-hint: "[base-ref]   # default: main. Use 'origin/main' for pre-push checks."
---

# /klai:tenant-review

Run het `klai-tenant-review` agent op de huidige diff. Output: gestructureerde lijst met HARD findings (blockers) en SOFT findings (review-items), elk geanchored op `file:line` met verwijzing naar de relevante sectie van `standards.md`.

## Default behavior

Vergelijkt huidige working tree + commits tegen `main`. Voor pre-push of pre-PR checks: gebruik `origin/main`.

## Wanneer gebruiken

- **Voor `git push` op een feature-branch** — vang regressies vóór CI ze vangt
- **Voor PR-merge** — sanity check dat de changes alignen met `standards.md`
- **End-of-session** — als je tenant-relevante files hebt aangeraakt
- **Bij review van iemand anders' PR** — checkt out the branch + run dit

## Wat het NIET doet

- Geen full security audit (gebruik `klai-security-audit` agent)
- Geen lint / format / type check (dat is `ruff check` / `pyright` / CI)
- Geen impact-analysis (gebruik CodeIndex `impact`)
- Geen merge-conflict resolution

## Process

1. Bepaal base-ref (uit args of default `main`)
2. Run `git fetch origin <base-ref>` (silent — keep cache fresh)
3. Run `git diff $base-ref --stat` om te tonen welke files in scope zijn
4. Spawn `klai-tenant-review` agent via natural-language delegation
5. Print de structured output

## Voorbeelden

```
/klai:tenant-review                    # diff vs local main
/klai:tenant-review origin/main        # diff vs origin (pre-push)
/klai:tenant-review main..feature/xyz  # specific branch
```

## Implementation

```bash
# 1. Resolve base-ref
BASE="${1:-main}"

# 2. Show scope
echo "Tenant-isolation review op diff vs $BASE"
git fetch origin --quiet 2>&1 || true
git diff "$BASE" --stat | tail -20

# 3. Delegate to agent
# Use the klai-tenant-review subagent to apply all 15 checks from
# .claude/skills/klai/tenant-isolation-checks/SKILL.md against the diff
# vs $BASE. Output the structured report (HARD findings, SOFT findings,
# confidence). If there are no tenant-relevant files in the diff, return
# "Geen tenant-relevant changes — review skipped."
```

## Skip-condition

Als de diff alleen raakt:
- `*.md` (docs only)
- `.github/workflows/*.yml` zonder code-impact
- `package*.json` / `uv.lock` zonder pyproject.toml change
- frontend-only `klai-portal/frontend/**`

Skip de review en print: "Geen tenant-relevant changes — review skipped."

## Output

Direct naar de gebruiker (niet naar een file). Voor archivering — output kopiëren naar PR-comment of naar `reports/audit-tenant-isolation-<date>/diff-review-<branch>.md` indien gewenst.
