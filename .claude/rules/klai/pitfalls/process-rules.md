---
severity_map:
  validate-before-code-change: { severity: 0.8, confirmed: 5, false_positives: 0 }
  verify-completion-claims: { severity: 1.0, confirmed: 8, false_positives: 0 }
  server-restart-protocol: { severity: 1.0, confirmed: 3, false_positives: 0 }
  test-user-facing-not-imports: { severity: 0.8, confirmed: 3, false_positives: 0 }
  debug-logging-first: { severity: 0.8, confirmed: 4, false_positives: 0 }
  trust-user-feedback: { severity: 1.0, confirmed: 3, false_positives: 0 }
  read-spec-first: { severity: 1.0, confirmed: 5, false_positives: 0 }
  minimal-changes: { severity: 0.8, confirmed: 6, false_positives: 0 }
  wait-after-question: { severity: 0.8, confirmed: 4, false_positives: 0 }
  listen-before-acting: { severity: 1.0, confirmed: 3, false_positives: 0 }
  ask-before-retry: { severity: 0.8, confirmed: 3, false_positives: 0 }
  debug-data-before-theory: { severity: 0.8, confirmed: 3, false_positives: 0 }
  verify-full-flow: { severity: 0.8, confirmed: 2, false_positives: 0 }
  check-process-not-curl: { severity: 0.8, confirmed: 2, false_positives: 0 }
  evidence-only-confidence: { severity: 1.0, confirmed: 1, false_positives: 0 }
  report-confidence: { severity: 0.8, confirmed: 1, false_positives: 0 }
  adversarial-at-high-confidence: { severity: 0.8, confirmed: 1, false_positives: 0 }
  diagnose-before-fixing: { severity: 0.8, confirmed: 1, false_positives: 0 }
  verify-system-not-just-feature: { severity: 0.8, confirmed: 1, false_positives: 0 }
  search-all-case-variants: { severity: 0.8, confirmed: 1, false_positives: 0 }
  convention-change-blast-radius: { severity: 0.8, confirmed: 1, false_positives: 0 }
---

# Process Rules

## validate-before-code-change
Before changing code to fix a bug, validate your hypothesis with real
data — logs, DB query, API response. One root cause = one fix attempt.
If the data doesn't confirm your hypothesis, form a new one instead
of patching speculatively.

## verify-completion-claims
Completion claims require verification with `git diff --stat`, `wc -l`,
or test output. Detailed metrics without matching file changes are a
hallucination signal — confirm every claim against observable artifacts
before reporting done.

## server-restart-protocol
Start and restart services using restart scripts or
`docker compose restart`, with the output visible in the foreground.
Background-launching a server with `run_in_background=true` hides
startup failures and crashes silently.

## test-user-facing-not-imports
After a migration, refactor, or bugfix, test the actual user-facing
functionality — not just that the module imports cleanly. A successful
import proves nothing about runtime behavior.

## debug-logging-first
When investigating API or integration errors, add debug logging first
to see the actual payload and response. Read the real data before
forming any hypothesis or writing any fix.

## trust-user-feedback
When a user reports something is broken but your tests pass, stop and
reproduce the exact scenario they described with all their parameters.
The user's environment is the ground truth — your test setup may be
missing a key variable.

## read-spec-first
Before implementing a SPEC or feature, read the full SPEC document in
`.moai/specs/` or `.workflow/specs/`. Starting implementation before
reading the spec leads to rework when requirements turn out to be
different from assumptions.

## minimal-changes
Make only the changes that were explicitly requested. Resist the urge
to "improve" surrounding code, refactor adjacent functions, or update
formatting in files you didn't need to touch. Unasked changes introduce
risk without authorization.

## wait-after-question
After asking the user a question, stop and wait for their answer. Do
not continue with tool calls in the same response — the answer may
change what you need to do next.

## listen-before-acting
Read the user's entire message before taking any action. Summarize
your understanding of what they want before starting work. Acting on
the first sentence often means missing critical context in the rest.

## ask-before-retry
After two failed attempts at the same operation, stop and ask the
user for guidance. Summarize what you tried and what happened — a
third blind retry rarely succeeds where the first two failed.

## debug-data-before-theory
When investigating unexpected behavior, examine actual data first —
logs, DB state, API responses. Form your theory from the evidence,
not the other way around. Theories without data lead to speculative
fixes.

## verify-full-flow
After fixing a bug in a multi-step pipeline, verify all downstream
steps still work — not just the step you touched. A fix that breaks
the next stage is not a fix.

## check-process-not-curl
To check if a server is running, use `lsof -nP -iTCP:PORT -sTCP:LISTEN`.
Curl without timeouts hangs indefinitely — if you must use curl, always
pass `--connect-timeout 2 --max-time 3`.

## evidence-only-confidence
Completion claims require observable evidence: test output, curl
response, log output, browser verification. "Code looks correct" and
"should work" score zero — only verifiable signals count toward
confidence.

## report-confidence
End completion messages with `Confidence: [0-100] — [evidence summary]`.
The stop hook enforces this mechanically — without a confidence number
backed by evidence, the session cannot end.

## adversarial-at-high-confidence
At confidence >= 80, ask yourself "what bugs can I find in what I just
did?" Frame as bug-hunting, not confirmation — "is this correct?"
triggers confirmation bias. The stop hook enforces this at >= 80.

## diagnose-before-fixing
When something breaks: stop, read logs first, form one hypothesis, try
one fix. If that fails, form a new hypothesis from the new evidence —
don't try random fixes hoping something sticks. After two failed fixes,
escalate to the user.

## verify-system-not-just-feature
After code changes, verify the system works end-to-end — not just the
feature you touched. Tests passing doesn't mean the entry point (API,
dashboard, service) is reachable. Check what the user actually uses.

## search-all-case-variants
When renaming across the codebase, search all case variants: kebab-case,
snake_case, camelCase, PascalCase, SCREAMING_SNAKE. A rename of
"bot-manager" requires checking bot_manager, botManager, BotManager,
BOT_MANAGER — missing one breaks silently.

## convention-change-blast-radius
When changing a default value or naming convention, search the entire
codebase for consumers — not just the files in your plan. Defaults have
unbounded blast radius: every file that hardcodes the old value is a
consumer you must update.
