# SPEC-SEC-002 Progress

**Status:** COMPLETE
**Updated:** 2026-04-03

## Evidence

### Groep 1 — CI/CD Security (R1-R4)
- R1: Trivy container image scanning in portal-api CI workflow
- R2: Semgrep SAST scanning in portal-api CI workflow
- R3: CVE-2026-4539 gedocumenteerd in vulnerability-management.md + GitHub Issue referentie
- R4: GitHub branch protection op `main` (PR review, status checks, force-push geblokkeerd)

### Groep 2 — Infrastructuur (R5-R6)
- R5: NTP-synchronisatie via `timedatectl set-ntp true` in setup.sh
- R6: GlitchTip SMTP geconfigureerd via SOPS (`GLITCHTIP_EMAIL_URL` → cloud86-host.io:587)

### Groep 3 — Applicatie (R7)
- R7: GitHub offboarding geïmplementeerd in `app/services/github.py` + geïntegreerd in `offboard_user()` (users.py:445), met `github_username` veld + Alembic migratie

### Groep 4 — Beleidsdocumenten (R8-R14)
- R8: backup-policy.md gecorrigeerd: age encryption, Qdrant als derived index, VictoriaLogs 30-dag retentie
- R9: Verwerkingsregister aangemaakt (`klai-private/compliance/policies/processing-register.md`)
- R10: Transitiechecklist eerste niet-oprichter aanstelling in personnel-screening.md (v1.1.0)
- R11: MFA-beleid in acceptable-use.md: TOTP, FIDO2, SMS-verbod, mfa_policy veld (v1.1.0)
- R12: SOPS sleutelrevocatieprocedure in vulnerability-management.md (v1.1.0) + offboarding checklist Tier 3 in personnel-screening.md (v1.2.0)
- R13: Eerste MongoDB hersteltest uitgevoerd en gedocumenteerd (`klai-private/compliance/spec/restore-test-log.md`, 2026-03-27)
- R14: backup.sh commentaarcorrectie (30 dagen) + SoA A.7.7 status → COVERED
- R14: asset-register.md Qdrant entry gecorrigeerd (v1.1.0)

### Groep 5 — Automatisch dependency-beheer (R15)
- R15: `.github/dependabot.yml` geconfigureerd (pip, npm, docker, github-actions)
- R15: Auto-merge workflow voor patch-updates
- R15: CVE-ignore migratie naar dependabot.yml
