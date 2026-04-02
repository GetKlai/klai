# SPEC-CONFIDENCE-001: Evidence-Based Confidence Protocol & Framework Hardening

**Status:** Draft
**Created:** 2026-04-02
**Author:** Mark + Claude
**Inspired by:** [Vexa Conductor](https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime/conductor)

## Problem Statement

Agents declare work "complete" without verifiable evidence. Process rules exist as prompt instructions but are routinely ignored under task pressure. Pitfalls document mistakes but lack machine-readable structure for automated enforcement. There is no mechanism to *prevent* an agent from stopping prematurely — only prompt-level suggestions that the agent treats as optional.

Vexa's Conductor framework spent $50 and 48 hours learning the same lesson, documented in their LEARNINGS.md:

> "The only rules that hold are ones enforced by the system, not by the prompt."

## Objectives

1. **Mechanically prevent** agents from stopping without evidence-based confidence reporting
2. **Add adversarial self-assessment** that fires automatically at high confidence
3. **Make pitfalls machine-readable** with severity scores for future automated enforcement
4. **Introduce structured gotcha tracking** for AI-specific failure patterns
5. **Extend process rules** with evidence-based completion standards
6. **Keep everything MoAI-update-proof** — zero changes to MoAI internals

## Architecture Constraint

```
MoAI eigendom (NIET aanraken)          Deze SPEC (onafhankelijke laag)
──────────────────────────────          ────────────────────────────────
.claude/skills/moai-*                   .claude/rules/klai/confidence.md
.claude/agents/*.md                     .claude/rules/klai/gotchas.md
.claude/commands/moai:*                 .claude/rules/klai/pitfalls/*.md (uitbreiden)
                                        .claude/rules/klai/patterns/*.md (uitbreiden)
                                        .claude/rules/klai/pitfalls/process-rules.md (uitbreiden)
                                        scripts/confidence-check.sh
                                        .claude/settings.json (hooks toevoegen)
```

Geen imports, referenties, of afhankelijkheden naar MoAI-internals. MoAI-updates raken skills, agents, en commands — nooit `rules/klai/` of `scripts/`.

---

## Requirements

### REQ-1: Stop Hook — Confidence Enforcement

**EARS:** When an agent attempts to stop, the system SHALL verify that a confidence level (0-100) based on observable evidence has been reported in the agent's last message.

**Acceptance Criteria:**

- AC-1.1: A bash script `scripts/confidence-check.sh` exists and is executable
- AC-1.2: The script reads the Stop hook input JSON (`last_assistant_message`, `stop_hook_active`, `transcript_path`)
- AC-1.3: The script blocks (returns `{"decision":"block","reason":"..."}`) if no confidence number (pattern: `confidence:?\s*[0-9]+` or `[0-9]+/100`) is found in the last message or recent transcript (last 50 lines)
- AC-1.4: Anti-loop protection: if `stop_hook_active` is `true`, the script exits 0 (allows stop) to prevent infinite blocking
- AC-1.5: `.claude/settings.json` registers the script as a Stop hook with timeout 10s
- AC-1.6: Existing PreToolUse hooks in settings.json are preserved (merge, not overwrite)
- AC-1.7: The script logs decisions to `/tmp/klai-confidence-hook.log` for debugging

**Evidence required for completion:**
- Run a Claude session, attempt to stop without reporting confidence → hook blocks
- Report confidence → hook allows stop
- Verify existing PreToolUse hook still fires

### REQ-2: Adversarial Self-Assessment at High Confidence

**EARS:** When the reported confidence is >= 80, the system SHALL verify that an adversarial self-assessment was performed before allowing the agent to stop.

**Acceptance Criteria:**

- AC-2.1: The Stop hook script checks for adversarial language when confidence >= 80 (patterns: `bug`, `could.*(wrong|fail|break)`, `risk`, `issue.*(found|remain)`, `adversarial`)
- AC-2.2: If confidence >= 80 and no adversarial check found, the script blocks with reason: "Confidence >= 80 but no adversarial self-check. Ask: what bugs can I find in what I just did?"
- AC-2.3: If confidence < 80, the adversarial check is skipped (not required for moderate confidence)

**Evidence required for completion:**
- Report "Confidence: 85" without adversarial text → hook blocks
- Report "Confidence: 85 — tested, no bugs found in review" → hook allows
- Report "Confidence: 60" without adversarial text → hook allows

### REQ-3: System Health Verification for Code Changes

**EARS:** When the agent performed code changes, deployments, or service operations, the system SHALL verify that system health was checked before allowing the agent to stop.

**Acceptance Criteria:**

- AC-3.1: The Stop hook detects code work via patterns in the transcript: `edit.*file`, `deploy`, `docker.*(up|restart)`, `compose.*up`, `curl.*localhost`, `pip install`, `npm`, `make`, `build`
- AC-3.2: When code work is detected, the hook checks for health verification language: `health`, `verified`, `dashboard.*(load|reach)`, `api.*(respond|return)`, `service.*(healthy|running)`, `curl.*200`
- AC-3.3: When no code work is detected (planning, docs, research), the health check is skipped
- AC-3.4: Block reason includes: "No system health verification. Before stopping, verify the user can reach the entry point."

### REQ-4: Confidence Protocol Rule File

**EARS:** The system SHALL provide a rule file that loads in every Claude session and defines the evidence-based confidence protocol.

**Acceptance Criteria:**

- AC-4.1: File `.claude/rules/klai/confidence.md` exists with NO `paths:` frontmatter (loads every session — intentionally, like process-rules.md; small footprint)
- AC-4.2: Contains an evidence scoring table:

| Signal | Counts as evidence | Score impact |
|---|---|---|
| Test suite passes | Yes | Strong (+) |
| curl/API returns expected response | Yes | Medium (+) |
| Verified in browser/logs | Yes | Medium (+) |
| Build compiles clean | Weak | Weak (+) |
| "Code looks correct" | No | 0 |
| "Should work" / "I believe" | No | 0 |
| "Reviewed the code" | No | 0 |
| Test fails | Yes | Strong (-) |
| Error in logs | Yes | Medium (-) |
| Retry needed | Yes | Weak (-) |

- AC-4.3: Documents the adversarial check protocol: at confidence >= 80, ask "What bugs can I find in what I just did?" — not "Is this correct?" (confirmation bias)
- AC-4.4: Documents stagnation detection: if confidence hasn't moved > 2 points in 5 steps → escalate to user
- AC-4.5: Documents oscillation detection: if confidence swings > 15 points in both directions within 4 steps → escalate
- AC-4.6: Defines the reporting format: `Confidence: [0-100] — [one-line evidence summary]`
- AC-4.7: Total file size under 60 lines (compact, like process-rules.md)

### REQ-5: Process Rules Extension

**EARS:** The process rules compact table SHALL be extended with confidence-related entries.

**Acceptance Criteria:**

- AC-5.1: Add entry `evidence-only-confidence` (CRIT): "Never claim completion based on 'code looks correct' or 'should work'. Observable evidence only: test output, curl response, log output, browser verification."
- AC-5.2: Add entry `adversarial-at-high-confidence` (HIGH): "At confidence >= 80, ask 'what bugs can I find?' before declaring done. Never ask 'is this correct?' (confirmation bias)."
- AC-5.3: Add entry `diagnose-before-fixing` (HIGH): "When something breaks, STOP. Read logs, form hypothesis, report what evidence would raise confidence. Do NOT start patching without root cause. Max 1 fix per hypothesis."
- AC-5.4: Add entry `report-confidence` (HIGH): "End completion messages with 'Confidence: [0-100] — [evidence]'. The Stop hook enforces this mechanically."
- AC-5.5: The full-text `process.md` is updated with expanded descriptions for each new entry, following existing format (Severity, Trigger, detailed explanation)
- AC-5.6: Existing 14 entries are NOT modified

### REQ-6: Machine-Readable Pitfall Severity

**EARS:** All pitfall files SHALL have machine-readable severity metadata in their YAML frontmatter.

**Acceptance Criteria:**

- AC-6.1: Each pitfall file's existing YAML frontmatter is extended with a `severity_map` field:
```yaml
---
paths:
  - "**/*.py"
severity_map:
  httpx-timeout: { severity: 0.8, confirmed: 5, false_positives: 0 }
  async-gather-swallow: { severity: 0.9, confirmed: 3, false_positives: 0 }
---
```
- AC-6.2: Severity values use the range 0.0-1.0 (mapping: CRIT=1.0, HIGH=0.8, MED=0.5, LOW=0.3)
- AC-6.3: `confirmed` count starts at 1 for all existing entries (they were all confirmed at least once to be documented)
- AC-6.4: `false_positives` starts at 0 for all existing entries
- AC-6.5: The following pitfall files are updated:
  - `backend.md` (8 entries)
  - `code-quality.md` (2 entries)
  - `devops.md` (3 entries)
  - `infrastructure.md` (15 entries)
  - `platform.md` (33 entries)
  - `security.md` (2 entries)
  - `docs-app.md` (6 entries)
  - `vexa-leave-detection.md` (9 entries)
- AC-6.6: `process-rules.md` and `git.md` (no `paths:` frontmatter) are NOT changed — they always load and have their own compact format
- AC-6.7: The severity_map does not break existing rule loading — Claude Code ignores unknown frontmatter keys
- AC-6.8: Index tables in each file get a new "Sev (numeric)" column showing the 0.0-1.0 value

### REQ-7: Pattern Files — Evidence Gates

**EARS:** Pattern files SHALL include an "Evidence" column in their index tables indicating how to verify correct application.

**Acceptance Criteria:**

- AC-7.1: Each pattern file's index table gets an "Evidence" column with one-line verification method
- AC-7.2: Example for `patterns/backend.md`:

| Pattern | When to use | Evidence |
|---|---|---|
| async-httpx-client | External HTTP calls in FastAPI | `pytest` passes with async mock |
| tenant-scoped-query | Any DB query for tenant data | Query includes `org_id` filter verified in test |

- AC-7.3: The following pattern files are updated:
  - `backend.md` (4 patterns)
  - `code-quality.md` (7 patterns)
  - `devops.md` (14 patterns)
  - `frontend.md` (7 patterns)
  - `infrastructure.md` (11 patterns)
  - `platform.md` (16 patterns)
  - `testing.md` (6 patterns)
- AC-7.4: `logging.md` is NOT changed (no index table, loaded via CLAUDE.md)
- AC-7.5: Evidence column entries are max 80 characters

### REQ-8: Gotcha System — AI Agent Failure Patterns

**EARS:** The system SHALL maintain a structured gotcha file tracking AI agent-specific failure patterns, separate from human-facing pitfalls.

**Acceptance Criteria:**

- AC-8.1: File `.claude/rules/klai/gotchas.md` exists with NO `paths:` frontmatter (loads every session — these are universal AI agent patterns)
- AC-8.2: Each gotcha entry has the structure:

```markdown
### G{N}: {title}

| Field | Value |
|---|---|
| Pattern | {what situation triggers this} |
| Root cause | {why it's a problem} |
| Mitigation | {what to do instead} |
| Severity | {0.0-1.0} |
| Confirmed | {N times} |
| False positives | {N times} |
| Last triggered | {date or "never"} |
| Source | {delivery failure / false blocker / human feedback / observation} |
```

- AC-8.3: Initial gotchas seeded from Vexa's lessons (adapted for Klai context):

| ID | Title | Severity | Source |
|---|---|---|---|
| G1 | Test the system, not just the feature | 0.8 | Vexa: 8/8 Playwright passed, dashboard unreachable |
| G2 | Diagnose before fixing — no flailing | 0.9 | Vexa: agent tried 4 fixes without root cause |
| G3 | Instructions alone don't change behavior | 0.7 | Vexa: confidence protocol ignored after restart |
| G4 | Search ALL case variants when renaming | 0.8 | Vexa: camelCase references missed in 107-file grep |
| G5 | Convention changes need full-codebase consumer search | 0.8 | Vexa: 14 stale references in unplanned files |
| G6 | Completion claims need git diff verification | 1.0 | Klai: verify-completion-claims rule exists but needs enforcement |
| G7 | Don't default to backward-compat fallbacks | 0.6 | Vexa: agent proposed shims instead of clean-cut rename |
| G8 | Agent testing can break the system under test | 0.7 | Vexa: curl bombardment killed nginx |

- AC-8.4: Total file size under 120 lines (index table + 8 gotchas)
- AC-8.5: The `/retro` skill can reference gotchas.md as a target for new AI-specific entries (but `/retro` skill itself is NOT modified — just documented as compatible)
- AC-8.6: Gotchas complement pitfalls: pitfalls = "human engineering mistakes", gotchas = "AI agent behavioral failures"

### REQ-9: Index Files Update

**EARS:** The pitfalls and patterns index files SHALL be updated to reflect all new files and metadata changes.

**Acceptance Criteria:**

- AC-9.1: `.claude/rules/klai/pitfalls.md` index updated:
  - New row for gotchas.md in context loading table
  - Note about severity_map in frontmatter
- AC-9.2: `.claude/rules/klai/patterns.md` index updated:
  - Note about Evidence column in pattern tables
- AC-9.3: `.claude/rules/klai/knowledge.md` updated:
  - New row for confidence protocol
  - New row for gotchas
  - Updated "When to read" section

### REQ-10: Documentation — Vexa Research Reference

**EARS:** The system SHALL document the Vexa Conductor research findings that informed this SPEC.

**Acceptance Criteria:**

- AC-10.1: File `.moai/specs/SPEC-CONFIDENCE-001/research.md` contains:
  - Summary of Vexa's LEARNINGS.md key findings
  - Comparison table: Conductor vs MoAI approach
  - List of adopted patterns with rationale
  - List of rejected patterns with rationale
  - Links to relevant Vexa source files on GitHub
- AC-10.2: Research references from Vexa's confidence-framework.md are preserved:
  - Kaddour et al. 2026 (agents 5.5x more likely confidently wrong)
  - Dunning-Kruger in LLMs (worst models = highest confidence)
  - Adversarial framing reduces overconfidence 15pp

---

## File Change Summary

### New files (4)

| File | Size estimate | Auto-loads? |
|---|---|---|
| `scripts/confidence-check.sh` | ~60 lines | N/A (hook script) |
| `.claude/rules/klai/confidence.md` | ~55 lines | Yes (every session) |
| `.claude/rules/klai/gotchas.md` | ~120 lines | Yes (every session) |
| `.moai/specs/SPEC-CONFIDENCE-001/research.md` | ~80 lines | No |

### Modified files (20)

| File | Change type | Risk |
|---|---|---|
| `.claude/settings.json` | Add Stop hook (merge with existing PreToolUse) | Low — separate keys |
| `.claude/rules/klai/pitfalls/process-rules.md` | Add 4 rows to table | Low — additive |
| `.claude/rules/klai/pitfalls/process.md` | Add 4 expanded entries | Low — additive |
| `.claude/rules/klai/pitfalls.md` | Update index | Low |
| `.claude/rules/klai/patterns.md` | Update index | Low |
| `.claude/rules/klai/knowledge.md` | Add 2 rows | Low |
| `.claude/rules/klai/pitfalls/backend.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/code-quality.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/devops.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/infrastructure.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/platform.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/security.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/docs-app.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/pitfalls/vexa-leave-detection.md` | Add severity_map to frontmatter + Sev column | Low |
| `.claude/rules/klai/patterns/backend.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/code-quality.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/devops.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/frontend.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/infrastructure.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/platform.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/testing.md` | Add Evidence column to index | Low |

### NOT modified (MoAI internals)

- `.claude/skills/moai-*` — geen wijzigingen
- `.claude/agents/*.md` — geen wijzigingen
- `.claude/commands/moai:*` — geen wijzigingen
- `CLAUDE.md` — geen wijzigingen

---

## Implementation Order

| Phase | Requirements | Effort | Dependencies |
|---|---|---|---|
| 1 — Core enforcement | REQ-1, REQ-2, REQ-3, REQ-4 | Klein | Geen |
| 2 — Process rules | REQ-5 | Klein | Geen |
| 3 — Gotcha system | REQ-8 | Klein | Geen |
| 4 — Pitfall severity | REQ-6 | Medium (8 files) | Geen |
| 5 — Pattern evidence | REQ-7 | Medium (7 files) | Geen |
| 6 — Index updates | REQ-9, REQ-10 | Klein | Na fase 1-5 |

Fasen 1-3 zijn onafhankelijk en kunnen parallel. Fase 4-5 zijn onafhankelijk en kunnen parallel. Fase 6 is een afsluiting na alle andere fasen.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Stop hook blokkeert te agressief | Agent kan niet stoppen bij korte taken | Anti-loop protection (`stop_hook_active`); hook checkt alleen laatste bericht + transcript tail |
| Extra token overhead door altijd-ladende files | Context window druk | confidence.md < 55 regels (~600 tokens); gotchas.md < 120 regels (~1200 tokens); totaal < 2000 tokens (~1% van 200K budget) |
| severity_map in frontmatter breekt rule loading | Rules laden niet meer | Claude Code negeert onbekende frontmatter keys — getest met bestaande `paths:` frontmatter die custom keys bevat |
| settings.json merge conflict bij MoAI update | Hooks verdwijnen | Hooks en permissions zijn aparte top-level keys; MoAI raakt alleen `permissions`; backup settings.json voor zekerheid |

---

## Verification Plan

### Fase 1 verificatie (Stop hook)
1. Start Claude sessie in klai project
2. Doe een kleine code wijziging
3. Probeer te stoppen zonder confidence → verwacht: hook blokkeert
4. Rapport "Confidence: 60 — ruff check passes" → verwacht: hook staat toe
5. Rapport "Confidence: 85 — tests pass" zonder adversarial → verwacht: hook blokkeert
6. Rapport "Confidence: 85 — tests pass, checked for bugs: none found" → verwacht: hook staat toe
7. Verifieer bestaande PreToolUse hook nog werkt: `git diff .claude/settings.json`

### Fase 4-5 verificatie (pitfalls/patterns)
1. Open een Python bestand → `backend.md` pitfalls laden met severity_map in frontmatter
2. Verifieer dat de severity_map keys niet in de gerenderde output verschijnen (frontmatter wordt gestript)
3. Verifieer dat bestaande pitfall entries ongewijzigd zijn (content diff)
