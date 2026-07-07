# MoAI-ADK Upgrade Research - 2026-07-07

## Conclusion

Target upgraded to the latest stable release found during research: `v2.14.0`.
The repo previously had mixed MoAI state: `.moai/config/sections/system.yaml`
reported `2.10.2`, while `.agency/fork-manifest.yaml` still records agency fork
origins at `v2.9.0`.

`v3.0.0-rc7` exists upstream, but was not selected because it is a release
candidate. I did not find official Codex integration in the investigated
`v2.14.0` or `v3.0.0-rc7` assets, and the upstream README documents Claude Code
plus GLM/CG modes rather than Codex. Codex support remains a Klai local overlay
via `AGENTS.md` and `.agents/codex/README.md`, not upstream MoAI behavior.

## Sources Checked

- GitHub releases: https://github.com/modu-ai/moai-adk/releases
- Upstream repository: https://github.com/modu-ai/moai-adk
- Upstream tags checked locally: `v2.10.2`, `v2.14.0`, `v3.0.0-rc7`

## What Changed

- Synced MoAI-owned Claude assets to `v2.14.0`:
  - `.claude/agents/moai/`
  - `.claude/commands/moai/`
  - `.claude/rules/moai/`
  - `.claude/hooks/moai/`
  - `.claude/skills/moai*`
  - `.claude/output-styles/moai/moai.md`
  - `.claude/output-styles/moai/einstein.md`
- Added new 2.14 assets:
  - `plan-auditor`
  - `/moai db`
  - `/moai design`
  - MoAI design constitution
  - LSP rule/config
  - expanded ast-grep rule directories
- Removed stale `.moai/config/astgrep-rules/go-hardcoding.yml`; upstream v2.14
  migrated that rule set to `.moai/config/astgrep-rules/go/hardcoding.yml`.
- Updated project version markers:
  - `.moai/config/sections/system.yaml` -> `2.14.0`
  - `.moai/config/sections/project.yaml` -> `2.14.0`
- Added 2.14 config fields while preserving Klai choices:
  - harness `default_profile`
  - harness `effort_mapping`
  - harness `plan_audit`
  - quality `ast_grep_gate`

## Local Overlays Preserved

- Root `AGENTS.md`
- `.agents/codex/README.md`
- `.claude/settings.json`
- `.claude/rules/klai/**`
- Klai E2E preflight in `.claude/commands/moai/e2e.md`
- Klai session-boundary context in `.claude/skills/moai/workflows/sync.md`
- Klai workflow/model choices in `.moai/config/sections/workflow.yaml`
- Klai git strategy and language settings

## Codex Support

Upstream MoAI remains Claude Code native. It supports Claude Code and GLM/CG
mode. I found no official Codex integration in the investigated `v2.14.0` or
`v3.0.0-rc7` assets. One upstream reference file,
`.claude/skills/moai-foundation-core/modules/agents-reference.md`, mentions
`ai-codex`, but that catalog does not match the actual v2.14.0 delivered
`.claude/agents/moai/` tree. A Klai overlay note was added there so agents do
not infer official upstream Codex support from stale upstream documentation.

For Klai, Codex support is local and should stay local:

- Codex reads `AGENTS.md`.
- Codex-specific behavior is documented in `.agents/codex/README.md`.
- Codex does not run Claude/MoAI hooks or `/moai` orchestration.

## Validation

- JSON parse: `.claude/settings.json`
- YAML parse: `.moai/config/sections/*.yaml`
- YAML parse: `.moai/config/astgrep-rules/**/*.yml`
- Shell syntax: `scripts/update-moai.sh`
- Shell syntax: `.claude/hooks/moai/*.sh`
- Asset presence: `/moai db`, `/moai design`, `plan-auditor`, LSP config, ast-grep config
- Output styles: upstream `moai.md` refreshed and `einstein.md` added
- Adversarial review fixes: `scripts/update-moai.sh` now preserves known Klai
  overlay files, syncs MoAI config assets/output styles, and avoids hardcoded
  per-user hook fallback paths.

## Residual Risk

- The `moai` CLI is not installed in this shell, so `moai version`, `moai update`,
  and `moai doctor` were not run.
- `.claude/settings.json` was intentionally not merged with upstream 2.14 hook
  registration because Klai has custom hook governance there. The MoAI hook
  wrapper files are updated, but full upstream hook activation remains an
  explicit follow-up decision.
- `.agency/fork-manifest.yaml` still records the original fork point
  `v2.9.0`. That is semantically a fork-origin marker, not the current MoAI
  asset version; hash-based drift checks may still report expected divergence.
