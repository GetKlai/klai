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
| Who blocks me at scan time? | `<service>.yml` workflow's `scan` job (STRICT-tier, exit-code 1). It runs on the pull request too: `quality.yml` fails a pull request that changes a Dockerfile unless that image's scan ran to success (`scripts/require-image-scans.py`). A new Dockerfile has to be added to the map in that script. Pull requests from forks cannot push the `:<sha>` image and therefore fail the build for services that scan a pushed image, as was already the case for connector, mailer and slack. |

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

## The code-scanning tab for third-party images

The Trivy alerts from `scan-pinned-images.yml` are an action list: every open one is something a person should do something about. On 2026-11-24 the tab held 1,275 alerts and nobody acted on any; the scan now triages each image's result with `scripts/triage-image-findings.py` before it uploads.

### What is scanned, and how often

Every image the stack can start, whatever the syntax of its pin: `image:` keys in `deploy/docker-compose.yml`, `docker-compose.dev.yml` and `deploy/docker-compose.gpu.yml`; images in environment values named `*_IMAGE` (the meeting bot's `BROWSER_IMAGE`, the tenant chat's `LIBRECHAT_IMAGE`) and the `slug=image` lists in `*_IMAGE_OVERRIDES`; and image defaults in the portal's settings that compose does not override. On 2026-11-25 that is 41 targets. `scripts/validate-vex.sh` fails when one of Renovate's custom regex managers treats a line in those files as an image pin that this derivation does not produce, so a new pin syntax cannot slip past the scan.

The scan runs daily (07:00 UTC) and on every push that changes a pin. A full run took 18 runner minutes on 2026-11-24. It rescans rather than re-joining stored results, because nothing is stored outside the runner. The remaining blind window after CISA adds a vulnerability to KEV is up to one day plus CISA's own delay from exploitation to listing. EPSS does not close that window: scores can sit near zero until the day a CVE is listed.

The scan no longer uses `--ignore-unfixed`. A finding without a fix is kept only when it is in KEV, or crosses the EPSS threshold on an image that is not internal.

### How a finding gets onto the tab

It is uploaded when at least one of these holds:

| Reason | Rule | Source of the number |
|---|---|---|
| `kev` | in the CISA Known Exploited Vulnerabilities catalog (joined through the CVE alias of an advisory id where there is one) | CISA KEV feed, downloaded once per run |
| `epss` | EPSS score at least 0.1 | FIRST EPSS API; 0.1 is well above FIRST's "every CVSS critical" effort level of 0.04 |
| `critical-exposed` | CRITICAL and fixable, on an image that is exposed, reachable, consumes untrusted input, or cannot be classified | exposure, below |
| `critical-overdue` | CRITICAL, fixable, published more than 90 days ago, on an internal image | FedRAMP 90-day tier, one step below exposed |

A kept finding is marked `overdue` when it is past its limit: 14 days after CISA added it to KEV (BOD 26-04's standard KEV tier), 30 days after publication for a critical on an image that is not internal (FedRAMP). The numbers live in one block at the top of the script.

Exposure classes, most exposed first, all derived from the repository except where noted:

- **exposed**: a `reverse_proxy` upstream in `deploy/caddy/Caddyfile` or in the code that writes tenant routes at runtime (portal-api's tenant chat routes, including the image in `LIBRECHAT_IMAGE` and its overrides; the docs-app routes), or a port published on every host interface.
- **content** (listed in `deploy/image-exposure.json`, each with a reason): parses untrusted input. These are the crawler, the scraper, the document converter and search; the meeting bot and the meeting API that receives its callbacks; the local model server; the PII analyzer and anonymizer; and the workers that parse incoming events or mail.
- **reachable**: shares a compose network with an exposed or content-consuming service, so it sits one hop behind one. A pre-authentication injection through a public application reaches it.
- **internal** (listed): none of the above. On 2026-11-25 no scanned production image is internal, because every compose network includes an exposed or content-consuming service. Only the developer compose stays internal. The `internal` list still records the intent for services that leave such a network.
- **unknown**: in none of the lists and not derived (today the three images of the GPU host). These are treated as exposed and named in the run summary.

A tracked file that mentions `reverse_proxy` and is not listed in `deploy/image-exposure.json` stops the scan, so a new route writer cannot leave an exposed image classified as internal.

### How a finding leaves the tab

- **Fixed**: the next scan no longer finds it (usually a Renovate bump), and GitHub closes it.
- **Below the rule**: it no longer meets any clause; the next upload omits it and GitHub closes it as fixed.
- **Stale SBOM**: Trivy reads every `*.spdx`, `*.spdx.json`, `*.cdx` and `*.cdx.json` in an image. An SBOM is treated as stale when its layer installed nothing itself and it lists packages at other versions than the ones installed anywhere in the image; that is a copied project directory, not an inventory. A vendor SBOM written with the software it describes (Bitnami's under `opt/bitnami/`, pip's list of vendored libraries) is kept. This rule never drops a KEV or high-EPSS finding; those always go to the keep rules, even when they come from a stale SBOM. Such a row can name a package the image does not have installed; check the installed version before you act on it.
- **VEX**: `.vex/unused-components.json` declares a component that is in the image but never executed by that service. Each statement has an OpenVEX justification, a reason, its evidence, at least one compose condition, `verified_against` (the image references it was checked on) and an expiry within 365 days. When a pin moves, a statement with `image_checks` is re-verified automatically by reading the named file from the new image. The gosu, litestream and kubectl statements work this way: the entrypoint script, and the runtime's default backend. A statement without `image_checks` fails the pull request's validator until someone re-verifies it and updates `verified_against`. The vaultwarden database-client statement works this way, because its evidence is the upstream default, not a file in the image. A statement that fails any check is not applied, so its findings come back. Statements describe components, never findings: the validator rejects a vulnerability identifier in these files.
- **Stale category**: a category the scan job no longer produces gets an empty upload, which is how GitHub closes the alerts of a configuration that stopped reporting.

### What is public and what is not

This repository is public, and so are its Actions logs, step summaries, artifacts and issues. None of them name a kept finding or say which image has one. The run summary gives whole-run counts only: images scanned, images with at least one finding on the tab, and findings per reason. The detail is on the code-scanning tab, for `main` and for a pull request's ref, which is not public. Raw scan results, triage output and OpenVEX documents stay on the runner. The report job counts the tab through the API, so no per-image result travels between jobs.

The pull-request gate fails with a generic message and never says which of several changed pins failed. A pull request that changes a single pin is different: its diff names the pin, so a red gate does say publicly that this incoming version has a known-exploited finding. That version is not running yet, which is why this residual disclosure is accepted, but it is not zero.

### What to do with one

1. `kev` on a running image: the run fails with "the image scan left findings that need action; see the code-scanning tab", and the usual `CI failure: Scan pinned external images` issue opens. Find the finding on the tab (filter by tool Trivy). Bump the pin today if a fixed upstream release exists; otherwise follow Recipe 6, step 3.
2. `critical-exposed`, `epss`, `critical-overdue`: bump the pin (or merge the Renovate pull request) within the limit.
3. The finding is real but the code never runs here: add a statement to `.vex/unused-components.json` with the evidence, a compose condition, `verified_against` and, where the evidence is a file in the image, `image_checks`. Then run `./scripts/validate-vex.sh --check-images` and open a pull request. Do not dismiss the alert: a dismissal survives the next scan and hides the next real finding in the same place.
4. A pull request that changes a pin runs `image-pin-triage.yml` through the required `quality` check. It fails if the new pin adds a KEV finding, on an image that is not internal, that neither the old pin nor the default branch's open alerts already had. A base tag that has disappeared upstream does not block the bump, because main's alerts stand in for it. The changed pins' findings are on the pull request's code-scanning view, under the categories `pin-triage-*`.

---

## Recipe 6 — A pinned external image (compose) introduces HIGH/CRITICAL

External images go through `scan-pinned-images.yml` (never blocks a push; triaged as described above) and, for a pull request that changes a pin, through `image-pin-triage.yml`. Surfaced via Security tab.

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
- SPEC-CI-TRIVY-POLICY-001 (historical — SPEC removed in repo cleanup 2026-08-18) — original SPEC, plan, acceptance scenarios
