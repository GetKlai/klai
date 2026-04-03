# SPEC-CONFIDENCE-001: Evidence-Based Confidence Protocol & Framework Hardening

**Status:** Completed (REQ-5 deferred)
**Created:** 2026-04-02
**Closed:** 2026-04-03
**Author:** Mark + Claude
**Inspired by:** [Vexa Conductor](https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime/conductor)

## Problem Statement

Agents declare work "complete" without verifiable evidence. Process rules exist as a compact table but are too sparse to activate during task execution — research shows that ~20-token table rows don't form enough associations for the model to pattern-match against its current behavior. There is no mechanism to *prevent* an agent from stopping prematurely — only prompt-level suggestions with a ~70% compliance ceiling.

Vexa's Conductor framework spent $50 and 48 hours learning the same lesson, documented in their LEARNINGS.md:

> "The only rules that hold are ones enforced by the system, not by the prompt."

Key research findings informing this SPEC:

- **IFScale (2026):** Claude Opus 4 drops from 100% compliance at 10 instructions to 44.6% at 500. Linear decay — every extra instruction costs compliance.
- **Anthropic:** Prompt-based rules achieve ~70% compliance maximum. Hooks achieve ~100%.
- **Kaddour et al. (2026):** Agents are 5.5x more likely to be confidently wrong than unsure about something right.
- **Pink Elephant problem (2024):** Transformers architecturally struggle with negation — "don't do X" activates the concept of X.

## Objectives

1. **Mechanically prevent** agents from stopping without evidence-based confidence reporting (hook = ~100% compliance)
2. **Add adversarial self-assessment** that fires automatically at high confidence
3. **Rewrite process-rules.md** from compact table to self-contained 2-3 sentence entries that activate during task execution
4. **Make pitfalls machine-readable** with severity tracking in YAML frontmatter
5. **Keep everything MoAI-update-proof** — zero changes to MoAI internals

## Architecture Constraint

```
MoAI eigendom (NIET aanraken)          Deze SPEC (onafhankelijke laag)
──────────────────────────────          ────────────────────────────────
.claude/skills/moai-*                   .claude/rules/klai/confidence.md
.claude/agents/*.md                     .claude/rules/klai/pitfalls/process-rules.md (herschrijven)
.claude/commands/moai:*                 .claude/rules/klai/pitfalls/process.md (herschrijven)
                                        .claude/rules/klai/pitfalls/*.md (severity_map toevoegen)
                                        .claude/rules/klai/patterns/*.md (evidence kolom toevoegen)
                                        scripts/confidence-check.py
                                        .claude/settings.json (hooks toevoegen)
```

Geen imports, referenties, of afhankelijkheden naar MoAI-internals. MoAI-updates raken skills, agents, en commands — nooit `rules/klai/` of `scripts/`.

---

## Design Decisions (uit review)

### D1: REQ-3 (system health check in hook) → opgeheven

De stop hook parst geen transcript op patronen als `docker.*up` of `curl.*200`. Dat is fragiel en Vexa-specifiek. Entry point verificatie wordt onderdeel van de evidence scoring tabel in confidence.md (REQ-3). De hook blijft dom: getal aanwezig? Adversarial bij ≥80? Klaar.

### D2: Gotchas geïntegreerd in process-rules

Geen apart `gotchas.md` bestand. Onze pitfalls zijn al AI-fouten (verify-completion-claims, validate-before-code-change, etc.). De gotcha-entries uit Vexa worden nieuwe process rules. Eén systeem met feedbackloop via severity_map, niet twee parallelle systemen.

### D3: Process-rules format — van compact tabel naar zelfstandige entries

**Onderzoek (IFScale, Anthropic docs):** Een compacte tabelrij (~20 tokens) is te vaag om associaties te vormen. Een volledige gotcha (~150-200 tokens) is te zwaar voor een altijd-ladend bestand. De sweet spot is 2-3 zinnen per entry (~50-100 tokens): concreet genoeg om te activeren, compact genoeg om niet te verdrinken.

Oud format:
```
| diagnose-before-fixing | HIGH | Something breaks | Read logs, max 1 fix |
```

Nieuw format:
```markdown
## diagnose-before-fixing
When something breaks: STOP. Read logs first, form one hypothesis,
try one fix. If that fails, form a new hypothesis — don't try random
fixes hoping something sticks. After 2 failed fixes, escalate to user.
```

De uitgebreide `process.md` blijft bestaan als on-demand referentie met voorbeelden, root cause, en source — maar process-rules.md is nu zelfstandig bruikbaar.

### D4: Severity labels helpen het model niet

Het model reageert niet op ernst-labels zoals een mens dat doet. "HIGH" in een tabel verandert nauwelijks gedrag. Severity tracking verhuist naar YAML frontmatter (machine-readable, nul token-kost) en verdwijnt uit de zichtbare content.

### D5: Positief framen, niet verbieden

Transformers hebben architecturaal moeite met negatie. "Don't try random fixes" activeert het concept "random fixes". Regels worden waar mogelijk positief geframed: "Read logs first, form one hypothesis, try one fix."

---

## Requirements

### REQ-1: Stop Hook — Confidence Enforcement

**EARS:** When an agent attempts to stop, the system SHALL verify that a confidence level (0-100) based on observable evidence has been reported in the agent's last message.

**Acceptance Criteria:**

- AC-1.1: A Python script `scripts/confidence-check.py` exists and is executable (cross-platform: macOS, Linux, Windows)
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

- AC-2.1: The Stop hook script checks for adversarial language when confidence >= 80 (patterns: `bug`, `could.*(wrong|fail|break)`, `risk`, `issue.*(found|remain)`, `adversarial`, `checked for`)
- AC-2.2: If confidence >= 80 and no adversarial check found, the script blocks with reason: "Confidence >= 80 requires adversarial self-check. Ask yourself: what bugs can I find in what I just did?"
- AC-2.3: If confidence < 80, the adversarial check is skipped (not required for moderate confidence)

**Evidence required for completion:**
- Report "Confidence: 85" without adversarial text → hook blocks
- Report "Confidence: 85 — tested, checked for bugs: none found" → hook allows
- Report "Confidence: 60" without adversarial text → hook allows

### REQ-3: Confidence Protocol Rule File

**EARS:** The system SHALL provide a rule file that loads in every Claude session and defines the evidence-based confidence protocol.

**Acceptance Criteria:**

- AC-3.1: File `.claude/rules/klai/confidence.md` exists with NO `paths:` frontmatter (loads every session — intentionally; small footprint)
- AC-3.2: Contains an evidence scoring table:

| Signal | Counts as evidence |
|---|---|
| Test suite passes | Yes — strong |
| curl/API returns expected response | Yes — medium |
| Verified in browser/logs | Yes — medium |
| Entry point reachable by user | Yes — medium |
| Build compiles clean | Weak |
| "Code looks correct" | No — scores 0 |
| "Should work" / "I believe" | No — scores 0 |
| "Reviewed the code" | No — scores 0 |

- AC-3.3: Documents entry point verification: for code changes, verify the user can reach the thing you changed (API responds, page loads, service healthy). For docs/planning, not required.
- AC-3.4: Documents the adversarial check protocol: at confidence >= 80, ask "What bugs can I find in what I just did?" — not "Is this correct?" (confirmation bias reduces overconfidence ~15pp)
- AC-3.5: Documents stagnation detection: if confidence hasn't moved > 2 points in 5 steps → escalate to user
- AC-3.6: Documents oscillation detection: if confidence swings > 15 points in both directions within 4 steps → escalate
- AC-3.7: Defines the reporting format: `Confidence: [0-100] — [one-line evidence summary]`
- AC-3.8: Total file size under 50 lines (~500 tokens)

### REQ-4: Process Rules Rewrite

**EARS:** The process rules file SHALL be rewritten from a compact reference table to self-contained entries that activate during task execution, and SHALL be extended with confidence-related and gotcha-derived entries.

**Acceptance Criteria:**

- AC-4.1: `process-rules.md` format changes from markdown table to markdown sections:
  - Each entry is a `## {id}` header followed by 2-3 sentences (~50-100 tokens)
  - Sentences include the trigger situation, what to do, and why (embedded, not as separate fields)
  - Positively framed where possible (what to do, not what to avoid)
  - No severity labels in visible content (model doesn't respond to them)

- AC-4.2: All 14 existing entries are rewritten to the new format. Content is preserved — only the format changes. Example:

Old:
```
| validate-before-code-change | HIGH | Fixing a bug based on an error | Validate hypothesis with real data before changing code. One root cause = one fix. |
```

New:
```markdown
## validate-before-code-change
Before changing code to fix a bug: validate your hypothesis with real
data (logs, DB query, API response). One root cause = one fix attempt.
If the data doesn't confirm your hypothesis, form a new one instead
of patching speculatively.
```

- AC-4.3: The following new entries are added (from Vexa gotchas and confidence protocol):

| New entry ID | Source |
|---|---|
| `evidence-only-confidence` | Confidence protocol — "code looks correct" = 0 |
| `report-confidence` | Confidence protocol — format and stop hook |
| `adversarial-at-high-confidence` | Kaddour et al. 2026 — adversarial framing |
| `diagnose-before-fixing` | Vexa G2/G3 — agent flailing without root cause |
| `verify-system-not-just-feature` | Vexa G1/G8 — tests pass but system broken |
| `search-all-case-variants` | Vexa G4 — missed camelCase in 107-file grep |
| `convention-change-blast-radius` | Vexa G5 — 14 stale refs in unplanned files |

- AC-4.4: YAML frontmatter added with severity_map for all entries (existing + new):
```yaml
---
severity_map:
  validate-before-code-change: { severity: 0.8, confirmed: 5, false_positives: 0 }
  evidence-only-confidence: { severity: 1.0, confirmed: 1, false_positives: 0 }
  search-all-case-variants: { severity: 0.8, confirmed: 1, false_positives: 0 }
---
```

- AC-4.5: Total file size target: ~21 entries × 75 tokens avg = ~1575 tokens + ~400 tokens frontmatter = ~2000 tokens. Current file is ~600 tokens — increase of ~1400 tokens is acceptable for dramatically better activation.

- AC-4.6: `process.md` (the expanded on-demand version) is updated with richer format per entry:
  - Existing fields: detailed explanation
  - New fields: **Root cause** (why this is a problem), **Source** (where we learned this — Vexa, incident, observation)
  - Loads on-demand via `paths:` frontmatter — serves as reference, not as the primary instruction source

### REQ-5: Machine-Readable Pitfall Severity ⛔ DEFERRED

> **Deferred (2026-04-03):** No consumer exists. Investigation of Vexa's source code revealed that their severity/confirmation tracking (which inspired this REQ) was aspirational documentation — never implemented. Gotchas G1-G11 are plain prose, check-completion.py does binary checks only. Adding severity_map without a consumer would be dead data (see pitfall `process-dead-data-in-frontmatter`). Revisit when a tool/hook is built that reads pitfall severity.

**EARS:** All pitfall files SHALL have machine-readable severity metadata in their YAML frontmatter.

**Acceptance Criteria:**

- AC-5.1: Each pitfall file's existing YAML frontmatter is extended with a `severity_map` field:
```yaml
---
paths:
  - "**/*.py"
severity_map:
  httpx-timeout: { severity: 0.8, confirmed: 5, false_positives: 0 }
  async-gather-swallow: { severity: 0.9, confirmed: 3, false_positives: 0 }
---
```
- AC-5.2: Severity values use the range 0.0-1.0 (mapping: CRIT=1.0, HIGH=0.8, MED=0.5, LOW=0.3)
- AC-5.3: `confirmed` count starts at 1 for all existing entries (confirmed at least once to be documented)
- AC-5.4: `false_positives` starts at 0 for all existing entries
- AC-5.5: The following pitfall files are updated:
  - `backend.md` (8 entries)
  - `code-quality.md` (2 entries)
  - `devops.md` (3 entries)
  - `infrastructure.md` (15 entries)
  - `platform.md` (33 entries)
  - `security.md` (4 entries)
  - `docs-app.md` (6 entries)
  - `vexa-leave-detection.md` (9 entries)
- AC-5.6: `git.md` (no `paths:` frontmatter, always loads) also gets severity_map — consistent with process-rules.md approach
- AC-5.7: The severity_map does not break existing rule loading — Claude Code ignores unknown frontmatter keys
- AC-5.8: Existing visible content (entries, index tables) is NOT changed — only frontmatter is added

### REQ-6: Pattern Files — Evidence Gates

**EARS:** Pattern files SHALL include an "Evidence" column in their index tables indicating how to verify correct application.

**Acceptance Criteria:**

- AC-6.1: Each pattern file's index table gets an "Evidence" column with one-line verification method
- AC-6.2: Example for `patterns/backend.md`:

| Pattern | When to use | Evidence |
|---|---|---|
| async-httpx-client | External HTTP calls in FastAPI | `pytest` passes with async mock |
| tenant-scoped-query | Any DB query for tenant data | Query includes `org_id` filter verified in test |

- AC-6.3: The following pattern files are updated:
  - `backend.md` (4 patterns)
  - `code-quality.md` (7 patterns)
  - `devops.md` (14 patterns)
  - `frontend.md` (7 patterns)
  - `infrastructure.md` (11 patterns)
  - `platform.md` (16 patterns)
  - `testing.md` (6 patterns)
- AC-6.4: `logging.md` is NOT changed (no index table, loaded via CLAUDE.md)
- AC-6.5: Evidence column entries are max 80 characters

### REQ-7: Index Files Update

**EARS:** The pitfalls and patterns index files SHALL be updated to reflect all changes.

**Acceptance Criteria:**

- AC-7.1: `.claude/rules/klai/pitfalls.md` index updated:
  - Note about severity_map in frontmatter across all pitfall files
  - Note about process-rules.md format change
  - Updated entry count for process-rules.md (14 → 21)
- AC-7.2: `.claude/rules/klai/patterns.md` index updated:
  - Note about Evidence column in pattern tables
- AC-7.3: `.claude/rules/klai/knowledge.md` updated:
  - New row for confidence protocol (confidence.md)
  - Updated "When to read" section with confidence-related guidance

### REQ-8: Documentation — Research Reference

**EARS:** The system SHALL document the Vexa Conductor research and LLM instruction-following research that informed this SPEC.

**Acceptance Criteria:**

- AC-8.1: File `.moai/specs/SPEC-CONFIDENCE-001/research.md` contains:
  - Summary of Vexa's LEARNINGS.md key findings
  - Comparison table: adopted vs rejected patterns with rationale
  - LLM instruction-following research findings (IFScale, Anthropic docs, Pink Elephant)
  - Links to relevant Vexa source files on GitHub
- AC-8.2: Research references preserved:
  - Kaddour et al. 2026 — agents 5.5x more likely confidently wrong
  - Dunning-Kruger in LLMs — worst models = highest confidence
  - Adversarial framing reduces overconfidence 15pp
  - IFScale 2026 — instruction compliance degradation data
  - Pink Elephant 2024 — negation is architecturally hard for transformers

---

## File Change Summary

### New files (2)

| File | Size estimate | Auto-loads? |
|---|---|---|
| `scripts/confidence-check.py` | ~60 lines | N/A (hook script) |
| `.claude/rules/klai/confidence.md` | ~45 lines | Yes (every session) |

### Rewritten files (2)

| File | Change | Risk |
|---|---|---|
| `.claude/rules/klai/pitfalls/process-rules.md` | Compact table → 2-3 sentence entries + severity_map frontmatter + 7 new entries | Medium — format change, content preserved |
| `.claude/rules/klai/pitfalls/process.md` | Add Root cause + Source fields to expanded entries + 7 new entries | Low — additive, on-demand loading |

### Modified files (16)

| File | Change type | Risk |
|---|---|---|
| `.claude/settings.json` | Add Stop hook (merge with existing PreToolUse) | Low — separate keys |
| `.claude/rules/klai/pitfalls.md` | Update index | Low |
| `.claude/rules/klai/patterns.md` | Update index | Low |
| `.claude/rules/klai/knowledge.md` | Add confidence row | Low |
| `.claude/rules/klai/pitfalls/backend.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/code-quality.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/devops.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/infrastructure.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/platform.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/security.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/docs-app.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/vexa-leave-detection.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/pitfalls/git.md` | Add severity_map to frontmatter | Low |
| `.claude/rules/klai/patterns/backend.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/code-quality.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/devops.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/frontend.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/infrastructure.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/platform.md` | Add Evidence column to index | Low |
| `.claude/rules/klai/patterns/testing.md` | Add Evidence column to index | Low |

### Existing file updated (1)

| File | Change | Risk |
|---|---|---|
| `.moai/specs/SPEC-CONFIDENCE-001/research.md` | Add LLM instruction-following research | Low |

### NOT modified (MoAI internals)

- `.claude/skills/moai-*` — geen wijzigingen
- `.claude/agents/*.md` — geen wijzigingen
- `.claude/commands/moai:*` — geen wijzigingen
- `CLAUDE.md` — geen wijzigingen

---

## Implementation Order

| Phase | Requirements | Effort | Dependencies |
|---|---|---|---|
| 1 — Stop hook + confidence protocol | REQ-1, REQ-2, REQ-3 | Klein | Geen |
| 2 — Process rules rewrite | REQ-4 | Medium | Geen |
| 3 — Pitfall severity | REQ-5 | Medium (9 files) | Geen |
| 4 — Pattern evidence | REQ-6 | Medium (7 files) | Geen |
| 5 — Index updates + docs | REQ-7, REQ-8 | Klein | Na fase 1-4 |

Fasen 1-4 zijn onafhankelijk en kunnen parallel. Fase 5 is afsluiting na alle andere fasen.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Stop hook blokkeert te agressief | Agent kan niet stoppen bij korte taken | Anti-loop protection (`stop_hook_active`); hook checkt alleen laatste bericht + transcript tail |
| Process-rules.md groeit van ~600 naar ~2000 tokens | Iets meer context pressure | IFScale data: 21 instructies is ruim onder de degradatiegrens; 2000 tokens = 1% van 200K budget |
| Process-rules format change breekt verwachtingen | Agents die het oude format verwachten | Geen agent refereert naar het format — alleen de content is relevant |
| confidence.md token overhead (~500 tokens) | Extra altijd-ladend bestand | Bewust compact gehouden; bevat alleen de evidence tabel en protocol — geen voorbeelden of uitleg |
| severity_map in frontmatter breekt rule loading | Rules laden niet meer | Claude Code negeert onbekende frontmatter keys — getest met bestaande frontmatter die custom keys bevat |
| settings.json merge conflict bij MoAI update | Hooks verdwijnen | Hooks en permissions zijn aparte top-level keys; MoAI raakt alleen `permissions`; backup settings.json voor zekerheid |

---

## Verification Plan

### Fase 1 verificatie (Stop hook + confidence protocol)
1. Start Claude sessie in klai project
2. Doe een kleine code wijziging
3. Probeer te stoppen zonder confidence → verwacht: hook blokkeert
4. Rapport "Confidence: 60 — ruff check passes" → verwacht: hook staat toe
5. Rapport "Confidence: 85 — tests pass" zonder adversarial → verwacht: hook blokkeert
6. Rapport "Confidence: 85 — tests pass, checked for bugs: none found" → verwacht: hook staat toe
7. Verifieer bestaande PreToolUse hook nog werkt: `git diff .claude/settings.json`

### Fase 2 verificatie (Process rules)
1. Open een nieuwe Claude sessie → process-rules.md laadt
2. Verifieer dat alle 21 entries geladen zijn (check context)
3. Token-meting: `wc -c process-rules.md` × 0.25 ≈ token count < 2500
4. Doe een taak die een regel triggert (bijv. bug fix) → verifieer dat het model de regel spontaan toepast

### Fase 3-4 verificatie (pitfalls/patterns)
1. Open een Python bestand → `backend.md` pitfalls laden met severity_map in frontmatter
2. Verifieer dat de severity_map niet in de gerenderde output verschijnt (frontmatter wordt gestript)
3. Verifieer dat bestaande pitfall entries ongewijzigd zijn (content diff)
4. Verifieer dat pattern Evidence kolom zichtbaar is

### Token budget verificatie
Altijd-ladende bestanden na implementatie:
- `process-rules.md`: ~2000 tokens (was ~600)
- `confidence.md`: ~500 tokens (nieuw)
- `git.md`: ~400 tokens (ongewijzigd)
- Totaal delta: +1900 tokens (~0.95% van 200K budget)
