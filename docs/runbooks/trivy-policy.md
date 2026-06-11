# Runbook — Trivy CVE policy

> Operational recipes for SPEC-CI-TRIVY-POLICY-001. Policy itself lives in
> `.claude/rules/klai/infra/deploy.md` § Trivy scanning. This runbook is the
> "how do I do X" companion.

## Quick reference

| Question | Answer |
|---|---|
| Where is severity policy defined? | `.trivy.yaml` at repo root |
| Where do exemptions live? | `<service>/.trivyignore.yaml` (e.g. `klai-portal/backend/.trivyignore.yaml`) |
| Who blocks me at commit time? | `.githooks/pre-commit` (after `git config core.hooksPath .githooks`) |
| Who blocks me at PR time? | `.github/workflows/validate-trivyignore.yml` |
| Who blocks me at scan time? | `<service>.yml` workflow's `scan` job (STRICT-tier, exit-code 1) |

---

## Recipe 1 — A new HIGH/CRITICAL CVE breaks my service's CI

### 1.1 Identify the finding

After CI failure on a `Build and push <service>` workflow `scan` job:

1. Open the failed run on GitHub Actions
2. Open the Security tab → Code scanning → filter by `analysis_key = .github/workflows/<service>.yml:scan`
3. Note: `id` (CVE / GHSA), `severity`, `package name`, and **fixed_version**

### 1.2 Decide: dep-bump or exemption

**Default to dep-bump.** Exemptions are friction by design — every entry needs rationale and expiry.

| Situation | Action |
|---|---|
| Fix-version exists in your dep tree | Bump the dep. For Python: `uv lock --upgrade-package <pkg>`. For Node: `npm update <pkg>` or bump in `package.json`. |
| Fix-version exists upstream but blocked by transitive | Try `npm-force-resolutions` / npm `overrides` / uv `[tool.uv.sources]` constraint |
| No fix-version yet (`ignore-unfixed: true` already filters these — but if it surfaces) | This is a Trivy DB lag; usually self-resolves within 24h. Re-run the workflow before doing anything. |
| Fix-version exists but exploiting requires conditions our deployment does not have | Document as exemption (recipe 2) |

### 1.3 If bumping: verify the bump cleared everything

After the dep-bump commit, push and watch the same workflow:

```bash
gh run watch $(gh run list --workflow <service>.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Re-check the Security tab — the alert should move to `state: fixed`.

---

## Recipe 2 — Add a documented exemption

Use this when a HIGH/CRITICAL is genuinely non-exploitable in our deployment context AND a dep-bump is not possible.

### 2.1 Create or edit the service's `.trivyignore.yaml`

Path convention: `<service-source-root>/.trivyignore.yaml`. Examples:
- `klai-portal/backend/.trivyignore.yaml`
- `klai-connector/.trivyignore.yaml`
- `klai-scribe/whisper-server/.trivyignore.yaml`
- `deploy/caddy/.trivyignore.yaml`
- `klai-docs/.trivyignore.yaml`

Required schema:

```yaml
vulnerabilities:
  - id: CVE-XXXX-NNNNN
    statement: |
      Concrete rationale ≥40 chars. Describe WHY this finding is non-
      exploitable in our deployment. State the attack vector and explain
      why it does not apply (e.g. "binary is unused at runtime", "user
      input never reaches this code path", "library is sandboxed").
      Boilerplate like "low priority" or "acceptable risk" is rejected
      mechanically — be specific.
    expired_at: 2026-09-01    # YYYY-MM-DD, max 12 months from today
    purls:                    # optional, lets Trivy match the right package version
      - "pkg:pypi/<pkg>@<version>"
```

Other valid sections (same per-entry rules): `secrets`, `misconfigurations`, `licenses`.

### 2.2 Reference the file from the workflow

If this is the first `.trivyignore.yaml` for the service, also add to the workflow's Trivy step:

```yaml
trivyignores: <path>/.trivyignore.yaml
```

### 2.3 Commit + verify

`.githooks/pre-commit` runs `scripts/validate-trivyignore.sh` automatically and rejects entries that don't carry the required fields. Same script runs in CI on `pull_request` via `validate-trivyignore.yml`.

To validate manually before committing:

```sh
./scripts/validate-trivyignore.sh klai-portal/backend/.trivyignore.yaml
```

Expected output: `[validate-trivyignore] <path>: <N> entries OK`.

---

## Recipe 3 — Rotate expired entries

The `expired_at` field exists to force re-evaluation. Once the date passes, the next workflow run treats the CVE as un-suppressed and CI goes red.

### 3.1 Find soon-expiring entries

```sh
# Lists every entry with expired_at within the next 30 days.
find . -name '.trivyignore.yaml' \
       -not -path './.git/*' -not -path './node_modules/*' -not -path './.venv/*' \
  | while read f; do
      python3 -c "
import yaml, sys, datetime
d = yaml.safe_load(open('$f')) or {}
for sec in ('vulnerabilities', 'secrets', 'misconfigurations', 'licenses'):
    for e in d.get(sec, []) or []:
        exp = e.get('expired_at')
        if exp:
            exp_d = datetime.date.fromisoformat(str(exp))
            days = (exp_d - datetime.date.today()).days
            if days < 30:
                print(f'$f :: {sec} :: {e[\"id\"]} :: {days} days left')
"
    done
```

### 3.2 For each soon-expiring entry, decide

- **Fix landed upstream?** → dep-bump (recipe 1.2/1.3) and remove the entry
- **Still un-fixable?** → renew with new `expired_at` (still ≤12 months) and update the `statement` to reflect why it remains un-exploitable
- **Was added in error?** → remove the entry; let CI block, then dep-bump

Never bulk-renew without re-evaluation. The friction is the feature.

---

## Recipe 4 — Run the smoke-test (proves the gate actually gates)

Periodically you want proof that STRICT-tier really blocks. Recommended cadence: once after any change to `.trivy.yaml` or the workflow Trivy step pattern.

### 4.1 Synthetic-CVE smoke-test

```sh
# Create throwaway branch
git checkout -b smoke/trivy-gate-test main

# Add a Dockerfile with a deliberately ancient base in any internal-image
# workflow's source tree. Example: append to klai-portal/backend/Dockerfile
# (you'll revert after the test).
#
# Use a base that Trivy DB definitely flags HIGH/CRITICAL on, e.g.:
#   FROM debian:11.4-slim AS smoke-test-base
#   RUN apt-get update && apt-get install -y --no-install-recommends openssl=1.1.1n-0+deb11u3 \
#       && rm -rf /var/lib/apt/lists/*
# (The pinned openssl version has known HIGH CVEs.)

git add klai-portal/backend/Dockerfile
git commit -m "smoke: synthetic HIGH CVE for Trivy gate verification — DO NOT MERGE"
git push -u origin smoke/trivy-gate-test
gh pr create --base main --draft --title "[SMOKE] Trivy gate verification" --body "Verifies SPEC-CI-TRIVY-POLICY-001 STRICT-tier blocks HIGH/CRITICAL. Auto-close after verification."
```

Expected outcome on the PR's `Build and push portal-api` workflow:
- `quality` job: SUCCESS
- `build-push` job: SUCCESS (image builds fine)
- `scan` job: **FAILURE** with exit-code 1 and SARIF showing the HIGH

If `scan` returns SUCCESS: the gate is broken. Investigate `trivy-config:` plumbing, `limit-severities-for-sarif:`, or `exit-code:` settings in the workflow. Do NOT merge anything until the gate is fixed.

### 4.2 Cleanup

Close the PR without merging. Delete the smoke branch:

```sh
gh pr close <PR-number>
git checkout main
git branch -D smoke/trivy-gate-test
git push origin --delete smoke/trivy-gate-test
```

---

## Recipe 5 — A new internal service needs a Trivy scan job

When adding `.github/workflows/<new-svc>.yml`:

1. Copy the scan-job pattern from any existing internal workflow (e.g. `knowledge-ingest.yml`)
2. Required step config:
   ```yaml
   - name: Run Trivy vulnerability scanner
     uses: aquasecurity/trivy-action@0.35.0
     with:
       image-ref: ghcr.io/getklai/<new-svc>:${{ github.sha }}
       trivy-config: .trivy.yaml
       format: 'sarif'
       output: 'trivy-results.sarif'
       exit-code: '1'
       limit-severities-for-sarif: 'true'
   - name: Upload Trivy SARIF to GitHub Security tab
     uses: github/codeql-action/upload-sarif@v4
     if: always()
     with:
       sarif_file: 'trivy-results.sarif'
   ```
3. Job-level `permissions: security-events: write` and `needs: build-push`
4. Push and verify the first run goes green (or red with documented findings → recipe 1 or 2)

If the new image has open HIGH/CRITICAL findings on first scan: do not weaken the workflow. Add a `<svc>/.trivyignore.yaml` per recipe 2 with each finding rationalised and an `expired_at` date.

---

## Recipe 6 — A pinned external image (compose) introduces HIGH/CRITICAL

External images go through `scan-pinned-images.yml` (WARN-tier — never blocks CI). Surfaced via Security tab.

1. Wait for next Renovate Monday cycle — most external base-images are auto-bumped
2. If urgent (active exploit in the wild): manual bump in `deploy/docker-compose*.yml`, then `gh workflow run scan-pinned-images.yml --ref main` to re-scan
3. If the latest stable upstream image is still vulnerable, do not patch a running container. Either open an upstream PR, publish and own a Klai-derived image, or document a temporary acceptance with exposure and re-assessment date in `VERSIONS.md`.
4. If image is locally-built (e.g. `vexaai/transcription-service:0.10.6-local-260503-0858`): not scannable — already filtered by SKIP-tier regex in `scan-pinned-images.yml` enumerate-step

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| pre-commit hook does nothing | Run `git config core.hooksPath .githooks` once per clone |
| `validate-trivyignore.yml` fails with "yaml module not available" | Workflow's `setup-python` step missing or `pip install pyyaml` failed — check workflow logs |
| Scan job is SUCCESS but the Security tab shows the CVE | This is correct behaviour — `ignore-unfixed: true` filters CVEs without fix-version from gating but still uploads them to SARIF for visibility |
| Scan job is SUCCESS for a CVE you expected to fail | Check `.trivyignore.yaml` doesn't have a stale matching entry; verify `trivy-config: .trivy.yaml` is set on the workflow's Trivy step |
| `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$` regex doesn't match my locally-built image | Tag must match the `infra/container-hygiene.md` convention exactly; legacy `<semver>-YYMMDD-HHMM` (no `-local-` infix) is NOT excluded by this regex |

## See also

- `.claude/rules/klai/infra/deploy.md` — § Trivy scanning (the rule)
- `.claude/rules/klai/infra/container-hygiene.md` — locally-built tag convention
- `.moai/specs/SPEC-CI-TRIVY-POLICY-001/` — original SPEC, plan, acceptance scenarios
