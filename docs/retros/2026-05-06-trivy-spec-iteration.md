# 2026-05-06 — SPEC-CI-TRIVY-POLICY-001 iteration retro

**Pitfalls (now live in):**
- `.claude/rules/klai/infra/deploy.md` § Trivy scanning (trivy-action 0.35.0 SARIF/severity bug + scan-job checkout + DB-lag noot)
- `.claude/rules/klai/lang/docker.md` § `apt purge --auto-remove` does not remove apt-hard-deps
- `.claude/rules/klai/pitfalls/process-rules.md` (diagnose-first when CI gates behave unexpectedly)

**Severity:** MEDIUM (no production outage; 4× force-push on a single PR cost a half-day)
**PRs:** [#410](https://github.com/GetKlai/klai/pull/410) (PR #1), [#411](https://github.com/GetKlai/klai/pull/411) (PR #2), [#415](https://github.com/GetKlai/klai/pull/415) (PR #4), [#417](https://github.com/GetKlai/klai/pull/417) (PR #3 gate flip), [#427](https://github.com/GetKlai/klai/pull/427) (#422 follow-up), [#436](https://github.com/GetKlai/klai/pull/436) (hotfix)

## What we shipped

In one afternoon, SPEC-CI-TRIVY-POLICY-001 went from "security theater" to "real CVE gate":

- Before: 9/10 internal-image scan jobs ran with `exit-code: '0'` (warn-only) and an inline `severity: 'CRITICAL,HIGH'` filter that the trivy-action wrapper silently bypassed for SARIF format. The 10th (portal-api) had `exit-code: '1'` by accident-of-omission and was failing every push to main on a MEDIUM pip CVE.
- After: all 10 internal-image scans gate on real HIGH/CRITICAL findings via direct trivy CLI; 4 new `.trivyignore.yaml` files document residuals with concrete runtime-exposure rationale and 2-month expiry; nghttp2 1.69.0-r0 patched in the caddy image via `apk upgrade`; pip-audit wired to klai-knowledge-mcp with 3 transitive CVEs lock-bumped.

Real CVE-fixes in main today (not just exemptions):
- `cryptography` 46.0.6 → 48.0.0 (CVE-2026-39892)
- `python-multipart` 0.0.22 → 0.0.27 (CVE-2026-40347)
- `pygments` 2.19.2 → 2.20.0 (CVE-2026-4539)
- `lxml` 6.0.2 → 6.1.0 (CVE-2026-41066, also made implicit dep explicit in klai-connector)
- `next` ^16.2.1 → ^16.2.4 (GHSA-q4gf-8mx6-v5v3)
- `picomatch` 2.3.1/4.0.3 → 2.3.2/4.0.4 (CVE-2026-33672)
- `nghttp2` 1.68.0 → 1.69.0 in caddy image (CVE-2026-27135)
- `gnupg` 9-of-10 packages purged from whisper image (CVE-2025-68973 latent surface reduced)
- CUDA base 12.6.3 → 12.9.1 (Ubuntu 24.04 security backports)
- Caddy `:latest` → `:2.11.2` (also closes pre-existing `infra/servers.md` rule violation)

## Lessons

### Lesson 1: trivy-action 0.35.0 (and 0.36.0) has a SARIF/severity-filter bug

**Symptom:** With `format: sarif` + `limit-severities-for-sarif: 'true'`, the `severity` filter is honoured for the SARIF body but **NOT** for the exit-code. A MEDIUM/LOW finding still triggers `exit-code: 1` even when the workflow asks for CRITICAL+HIGH gating.

**How we discovered it:** PR #3's first main run had three failures (caddy / docs / whisper-server) where the GitHub Code Scanning alert API showed only MEDIUM findings, but the scan job exit-code 1. Other 7 workflows happened to pass because `ignore-unfixed: true` cleared their finding lists to zero — the wrapper's bug never bit them.

**Diagnosis path that worked:** add a step running `trivy image --format table` on the same image (with `--exit-code 0` so it doesn't gate). The table output shows the actual CVE list at HIGH/CRITICAL — which made it clear the filter WAS working at detection level (5 findings shown for caddy, all HIGH/CRITICAL), but the wrapper layer was leaking unfiltered exit-code.

**Permanent workaround (now live):** all 11 scan jobs invoke trivy CLI directly via `aquasecurity/setup-trivy@e6c2c5e3` + `run: trivy image --severity CRITICAL,HIGH ...`. CLI honours `--severity` for both detection AND exit-code. `.trivy.yaml` stays as canonical policy reference; once trivy-action lands proper precedence we can collapse the inline flags back to wrapper inputs.

**Time cost:** ~3 hours debugging across 4 force-pushes. Could have been ~30 min if the diagnostic table-step had been the first action.

### Lesson 2: scan-jobs need `actions/checkout` for `trivy-config` and `trivyignores` to resolve

**Symptom:** Trivy logs `cannot find ignorefile 'klai-connector/.trivyignore.yaml'` when the workflow has `trivyignores:` set. Same for `.trivy.yaml` via `trivy-config:`.

**Cause:** trivy-action and trivy CLI both resolve these paths from `$GITHUB_WORKSPACE`. Without an `actions/checkout` step, the workspace is empty.

**Fix:** every scan-job's first step is `- uses: actions/checkout@v6` BEFORE the `Log in to GHCR` step. None of klai's 11 scan jobs had checkout pre-PR #3 — for ~9 months the `severity:` filter appeared not to work because the config-file simply wasn't on disk.

### Lesson 3: `apt purge --auto-remove` does not remove `gpgv`

**Symptom:** PR #2's whisper-server Dockerfile change ran `apt-get purge -y --auto-remove gnupg gnupg2`. Successfully removed 9 of 10 gnupg-family packages (`gpg`, `gpg-agent`, `gpgsm`, `dirmngr`, `gnupg-utils`, `gnupg-l10n`, `gpg-wks-server`, `gpg-wks-client`, `keyboxd`). But `gpgv` remained.

**Cause:** `gpgv` is a **hard dependency of `apt` itself** — apt uses gpgv to verify package signatures during install. `--auto-remove` cannot touch hard-deps of essential packages.

**Workaround (now live):** documented exemption in `klai-scribe/whisper-server/.trivyignore.yaml` for CVE-2025-68973 with rationale that gpgv only runs when apt-get install/update executes against signed repos. The runtime container never invokes apt — it runs uvicorn for inference. Vulnerable code-path requires a malicious keybox file, which has no path in the deployed image.

**Pattern for future:** `apt-get purge gnupg gnupg2` removes "user-facing" gpg tools but leaves the apt-internal gpgv. To remove gpgv specifically you need `apt-get purge --allow-remove-essential gpgv`, after which the image cannot run `apt install` anymore. Acceptable for runtime-only containers; not for build stages.

### Lesson 4: Caddy 2.11.2 ships still-vulnerable transitive Go-deps

**Symptom:** PR #2 pinned `caddy:2.11.2-alpine` expecting it to clear 6 HIGH/CRIT findings (smallstep, grpc, go-jose v3+v4, otel, nghttp2). After merge, Trivy on the rebuilt image still showed 5 of those at HIGH/CRITICAL.

**Cause:** Caddy 2.11.2's `go.mod` doesn't yet bump those transitive deps, even though upstream fixes have been published for weeks/months. xcaddy builds use Caddy's `go.sum` verbatim — overrides require forking Caddy or `go mod replace`.

**Workaround (now live):** documented exemptions in `deploy/caddy/.trivyignore.yaml` for CVE-2026-30836 (smallstep), CVE-2026-33186 (grpc), CVE-2026-34986 (go-jose v3+v4 — single entry covers both packages), CVE-2026-39883 (otel/sdk), CVE-2026-29181 (otel base). Each entry has concrete runtime-exposure analysis: no SCEP enabled, no `tracing` directive, no inbound JWE accept. Renovate watches caddy:* tags for next bump.

**The one finding we didn't ignore (real fix):** CVE-2026-27135 in nghttp2-libs. nghttp2 IS exposed via Caddy's HTTPS edge (clients use HTTP/2 by default), so an H2-DoS attack-vector is real. Real fix via Dockerfile `RUN apk update && apk upgrade --no-cache nghttp2 nghttp2-libs` — Alpine 3.23 main has the patched 1.69.0-r0 since 2026-04-28.

### Lesson 5: Trivy DB lag — package can be at upstream-fix-version and still flagged

**Symptom:** PR #2 bumped `picomatch` to 4.0.4 — the patched version per upstream advisory and per NVD. Trivy v0.69.3 still flags 4.0.4 instances for CVE-2026-33671.

**Cause:** Trivy DB advisory mappings sometimes lag npm's advisory database. Different CVE-ids (33671 vs 33672) for closely-related ReDoS issues compound the confusion.

**Workaround (now live):** documented exemption in `klai-docs/.trivyignore.yaml` with rationale explicitly citing NVD's affected-range (`< 4.0.4`) — the package is at the fix-version per upstream. Re-evaluate when Trivy DB recategorises.

### Lesson 6: Diagnose-first when a CI gate behaves unexpectedly

**Pattern that worked the second time:** add a no-op `--format table` step before the gating step. Table output shows actual CVE list, severities, fix-versions. SARIF format is opaque from stdout — only `Process completed with exit code 1` is visible.

**Time saved:** the diagnostic step took 90 seconds per workflow_dispatch, versus the ~30 minutes of pushing-and-praying that preceded it.

## What I would do differently

1. **Diagnose-step BEFORE first gate-flip.** I should have added the `--format table` step to one workflow on the PR #3 branch BEFORE pushing the gate-flip. Would have caught the trivy-action bug immediately, saving 4 force-pushes.
2. **Verify dep-bump assumptions BEFORE writing ignore-rationales.** I assumed Caddy 2.11.2 would clear 6 CVEs. Trivy diagnostic would have shown it cleared 1 of 6 (nghttp2 via base, NOT the Go deps). Cheaper than writing 4 rationales for the wrong CVE-ids.
3. **`pull_request:` triggers on every Trivy-bearing workflow.** caddy / docs / whisper had `push: branches: [main]` only. So scan failures only surfaced post-merge. Should have been on PRs too.
4. **`actions/checkout` audit at SPEC-time.** PR #3 added checkout to all scan jobs only after the trivyignores file lookup failed. This was a known requirement — should have been part of the initial workflow audit.

## Class of error

This was a **tooling-trust class** error — assuming trivy-action's input-key semantics matched Trivy CLI's flag semantics 1:1, when in fact the wrapper has a documented limitation around SARIF format that we didn't read carefully. Same flavour as the `:latest` rule violation that hid for months in the caddy Dockerfile.

Mitigation against this class: when a wrapper exists between us and a tool we depend on for security gating, prefer direct CLI invocation. The wrapper is convenience; the gate is critical.

## See also

- `.claude/rules/klai/infra/deploy.md` § Trivy scanning — current canonical reference
- `docs/runbooks/trivy-policy.md` — recipes for adding ignores, rotating expired entries, smoke-testing the gate
- `.moai/specs/SPEC-CI-TRIVY-POLICY-001/` — original SPEC, plan, acceptance scenarios
