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

## ui-bugfix-browser-verify
After fixing any frontend or UI bug, click through the actual browser
flow before reporting done. Code reading and "looks correct" score zero.
The Playwright MCP is available in every session — use it.

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

## search-all-case-variants
When renaming across the codebase, search all case variants: kebab-case,
snake_case, camelCase, PascalCase, SCREAMING_SNAKE. A rename of
"bot-manager" requires checking bot_manager, botManager, BotManager,
BOT_MANAGER — missing one breaks silently.

## serena-before-read
Before using Read to explore a `.py`, `.ts`, `.tsx`, or `.js` file, call
`get_symbols_overview` first to understand its structure. Only Read when you
know exactly which lines you need. Use `find_symbol` to locate specific
functions/classes, and `find_referencing_symbols` before editing to understand
impact. Fall back to Read/Grep only for non-code files.

## convention-change-blast-radius
When changing a default value or naming convention, search the entire
codebase for consumers — not just the files in your plan. Defaults have
unbounded blast radius: every file that hardcodes the old value is a
consumer you must update.

## spec-constraints-before-implement
Before writing a single line of implementation code, read the SPEC's
constraints section and write down how you will verify each point:
image tags (never :latest — use the exact tag or commit hash the SPEC
specifies), resource limits (check they match the SPEC's RAM budget),
and which services must NOT run. If any constraint is unclear, ask
before implementing.

## spec-architecture-mismatch-stop
If your implementation diverges architecturally from the SPEC —
monolith where microservices are described, subprocess where containers
are specified, or vice versa — STOP immediately. State the mismatch
explicitly and ask for confirmation before continuing. Never assume
"close enough."

## spec-violation-in-logs
If during debugging a log signal directly violates a SPEC constraint
(wrong service running, wrong memory usage, wrong routing) — STOP.
Report the violation. Do not continue debugging a downstream symptom
while an upstream SPEC violation is visible.
