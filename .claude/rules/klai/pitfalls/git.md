---
severity_map:
  git-no-destructive-commands: { severity: 1.0, confirmed: 1, false_positives: 0 }
  git-no-secrets-in-commits: { severity: 1.0, confirmed: 1, false_positives: 0 }
  git-commit-specific-files: { severity: 0.8, confirmed: 1, false_positives: 0 }
  git-verify-before-commit: { severity: 0.8, confirmed: 1, false_positives: 0 }
---
# Git Pitfalls

> Version control safety rules.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [git-no-destructive-commands](#git-no-destructive-commands) | CRIT | Never run reset/restore/clean without explicit confirmation |
| [git-no-secrets-in-commits](#git-no-secrets-in-commits) | CRIT | Never commit secrets or credentials |
| [git-commit-specific-files](#git-commit-specific-files) | HIGH | Stage specific files; never `git add -A` |
| [git-verify-before-commit](#git-verify-before-commit) | HIGH | Read `git diff` before committing |

---

## git-no-destructive-commands

**Severity:** CRIT

**Trigger:** Any time a git command would discard or rewrite work

NEVER run destructive git commands without explicit user confirmation:
- `git reset --hard`
- `git checkout .` or `git restore .`
- `git clean -f`
- `git push --force` to main/master
- `git branch -D`

**Rule:** If in doubt, stash first. Show the user what would be lost before running a destructive command.

---

## git-no-secrets-in-commits

**Severity:** CRIT

**Trigger:** Committing files that may contain credentials or API keys

NEVER commit secrets, credentials, API keys, or environment files. Check before every commit.

**Files to never commit:**
- `.env`, `.env.local`, `.env.production`
- `*.sops.env` (the decrypted version — only the encrypted version is safe)
- Files containing `SECRET`, `PASSWORD`, `TOKEN`, `KEY` in their name

**If a secret was accidentally committed:**
1. Do NOT just add another commit removing it
2. Treat the secret as compromised — rotate it immediately
3. Use `git filter-repo` or `git filter-branch` to remove it from history
4. Force-push only after confirming the secret is rotated

---

## git-commit-specific-files

**Severity:** HIGH

**Trigger:** Staging files for a commit

Prefer staging specific files by name rather than `git add -A` or `git add .`. Broad staging can accidentally include debug files, sensitive data, or unintended changes.

```bash
# Preferred
git add src/services/provisioning.py src/api/tenants.py

# Risky (may include unintended files)
git add .
```

---

## git-verify-before-commit

**Severity:** HIGH

**Trigger:** About to create a commit

Before committing, always run:
```bash
git diff --staged    # Review exactly what is staged
git status           # Confirm no unintended files
```

Never create a commit based solely on what the AI says was changed — verify with the actual diff.

---
