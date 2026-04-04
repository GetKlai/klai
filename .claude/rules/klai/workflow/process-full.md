---
paths:
  - "**/.workflow/specs/**"
  - "**/docs/specs/**"
  - "**/SPEC*.md"
---
# Process Pitfalls — Extended Reference

> Full descriptions with root causes and sources.
> Compact version (always loaded): `pitfalls/process-rules.md`

---

## Debugging & Investigation

### data-before-code (HIGH)
Before fixing a bug: check the logs and follow the actual code path. No guessing, no stacking patches.
Trace what happens at runtime. If data isn't visible, add debug logging and reproduce first.
One root cause confirmed by real data = one fix. If the first fix doesn't work, go back to data.
**Source:** Multiple incidents where agents applied 3-4 "fixes" without checking logs.

### debug-holistic-view (HIGH)
Zoom out before zooming in. Trace the full flow: where does data come from, what transforms it, what consumes it downstream?
Search the codebase for related patterns and callers. Search online for the error or library behavior.
**Source:** Agents fixating on the error line while the root cause was upstream.

### trust-user-feedback (CRIT)
User says broken + your tests pass → reproduce EXACT user scenario with ALL their parameters.
Agent's test setup may be missing key variables. User's environment is ground truth.
**Source:** Multiple incidents where different test inputs masked the real failure.

---

## Verification & Confidence

### verify-changes-landed (CRIT)
After completing work: 1) `git diff --stat` — right files changed? 2) Logs/health — service runs new code?
3) Browser (Playwright MCP) — for UI changes, click through actual flow.
AI hallucination pattern: prepares for work (imports, skeletons) → reports complete with fabricated metrics.
**Source:** Vexa Conductor — 4+ false completion claims from dev agent.

### report-confidence (HIGH)
End with `Confidence: [0-100] — [evidence summary]`. Only observable evidence counts.
"Code looks correct" and "should work" score zero. Stop hook enforces mechanically.
**Source:** Vexa Conductor — mechanical enforcement achieves ~100% compliance vs ~70% for prompt instructions.

### adversarial-at-high-confidence (HIGH)
At >= 80: ask "what bugs can I find?" not "is this correct?" Adversarial framing reduces overconfidence ~15pp.
**Source:** Kaddour et al. 2026 adversarial framing study.

---

## Communication & Flow

### communication-discipline (CRIT)
Read ENTIRE message before ANY action. Summarize understanding first.
After asking a question: STOP. No more tool calls — the answer may change everything.
**Source:** Agents starting tool calls after reading first line of multi-paragraph request.

### ask-before-retry (HIGH)
After 2 failures: stop, summarize what failed, ask user. Third blind retry rarely succeeds.
**Source:** Agents retrying the same failing approach 5+ times without asking.

---

## Scope & Changes

### minimal-changes (HIGH)
Only changes explicitly requested. No "improving" surrounding code, refactoring, or formatting untouched files.

### read-spec-first (CRIT)
Read FULL spec in `.moai/specs/` or `.workflow/specs/` before implementing. Title alone is never enough.

### search-broadly-when-changing (HIGH)
When renaming or changing defaults: search entire codebase for all consumers, all case variants
(kebab, snake, camel, Pascal, SCREAMING_SNAKE). Defaults have unbounded blast radius.
**Source:** Convention change → 14 stale references in 9 unplanned files. Missed camelCase in 107-file grep.

---

## SPEC Discipline

### spec-discipline (CRIT)
Before implementing: write down each SPEC constraint and how to verify it (image tags, resource limits, excluded services).
- Architecture diverges from SPEC → STOP, state mismatch, ask before continuing.
- Logs show SPEC constraint violation → STOP, report violation before debugging downstream.
- Constraint unclear → ask before implementing.
**Source:** SPEC-VEXA-001 — used `:latest` despite SPEC specifying commit hash. Implemented old monolith instead of three-service architecture. WhisperLive in logs (forbidden), continued debugging OOM.

---

## Meta-process

### no-prompt-files (HIGH)
Never write prompts/audit templates to .md files. Output in chat directly.

### spec-stub-deps-premature (HIGH)
Comment out new deps at stub time. Guard imports: `try: import X except ImportError: X = None`.
Real deps resolved in `/run` implementation phase.
**Source:** SPEC-KB-011 — graphiti-core pydantic conflict broke CI during spec.

### no-architecture-change-in-migration (CRIT)
Migration = same services, different server. NEVER consolidate or redesign during a move.
**Source:** SPEC-GPU-001 — agent replaced TEI + Infinity with single Infinity (GPU memory leak, no metrics).

### test-cache-two-level-dispatch (MED)
Multi-key cache mocks: dispatch by key prefix, not single return value.

### consolidate-overlapping-rules (MED)
Overlapping rules dilute each other. Merge into the more specific, actionable version.
