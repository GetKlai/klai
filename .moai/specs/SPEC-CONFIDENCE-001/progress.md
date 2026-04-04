# SPEC-CONFIDENCE-001 Progress

**Status:** COMPLETED
**Updated:** 2026-04-03

## Completed Requirements

| REQ | Description | Evidence |
|-----|-------------|---------|
| REQ-1 | Stop hook (confidence-check.py) | Script in scripts/, registered in settings.json |
| REQ-2 | Adversarial self-assessment at >= 80 | Integrated in stop hook script |
| REQ-3 | Confidence protocol rule file | .claude/rules/klai/confidence.md (always-loading, <50 lines) |
| REQ-4 | Process rules rewrite | 21 entries in process-rules.md, new format (2-3 sentences each) |
| REQ-4.6 | process.md Root Cause/Source fields | All 7 new entries + existing entries have Root cause/Source |
| REQ-6 | Pattern files evidence columns | Evidence column added to 7 pattern files |
| REQ-7 | Index files update | pitfalls.md, patterns.md, knowledge.md all updated |
| REQ-8 | Research reference document | .moai/specs/SPEC-CONFIDENCE-001/research.md complete |

## Deferred: REQ-5 (severity_map in pitfall frontmatter)

**Decision:** Deferred — no consumer exists.

REQ-5 specified adding `severity_map` YAML frontmatter (severity scores, confirmation counts,
false positive counts) to all pitfall files. This was inspired by Vexa Conductor's
confidence-framework.md, which describes a Bayesian model with severity tracking.

**Investigation (2026-04-03):** Verified against Vexa's actual source code:
- `confidence-framework.md` describes severity/confirmation tracking in detail
- `CLAUDE.md` gotchas (G1-G11) are **plain prose** — no severity, no counts, no structured metadata
- `check-completion.py` does **binary checks only** — no severity scores consumed

Vexa designed but never implemented the severity tracking. Their gotchas work as plain text
with narrative incident descriptions. The "severity + confirmation tracking" was aspirational
documentation, not a working system.

Adding severity_map to our pitfall files would be dead data — no tool, hook, or agent
reads YAML frontmatter values. This matches pitfall `process-dead-data-in-frontmatter`.

**Revisit when:** A consumer is built (e.g., a hook that adjusts behavior based on pitfall severity).
