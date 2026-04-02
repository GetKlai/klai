# Confidence Protocol

Evidence-based confidence reporting. The stop hook enforces this mechanically.

## Evidence Scoring

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

For code changes: verify the user can reach what you changed (API responds,
page loads, service healthy). For docs/planning: not required.

## Reporting Format

End completion messages with:

`Confidence: [0-100] — [one-line evidence summary]`

## Adversarial Check (at >= 80)

Ask yourself: "What bugs can I find in what I just did?"
Frame as bug-hunting, not confirmation. This reduces overconfidence ~15pp.

## Escalation

Stagnation: if confidence hasn't moved > 2 points in 5 steps, escalate to user.
Oscillation: if confidence swings > 15 points in both directions within 4 steps, escalate.
