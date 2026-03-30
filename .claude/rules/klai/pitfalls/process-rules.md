# Process Rules (Compact)

> Quick reference for every session. Full descriptions with examples: `.claude/rules/klai/pitfalls/process.md`

| ID | Sev | Trigger | Rule |
|---|---|---|---|
| validate-before-code-change | HIGH | Fixing a bug based on an error | Validate hypothesis with real data (logs, DB, API) before changing code. One root cause = one fix. |
| verify-completion-claims | CRIT | AI reports task complete with metrics | AI can hallucinate completion. Verify with `git diff --stat`, `wc -l`. Watch for detailed metrics without actual work. |
| server-restart-protocol | CRIT | Restarting a service | NEVER use `run_in_background=true` to start servers. Use restart scripts or `docker compose restart`. |
| test-user-facing-not-imports | HIGH | Completing a migration/refactor/bugfix | Test actual user-facing functionality, not just that a module imports. |
| debug-logging-first | HIGH | Investigating API/integration errors | Add debug logging FIRST to see actual data. Never implement fixes before seeing the real payload. |
| trust-user-feedback | CRIT | User says it's broken, your tests pass | Stop. Reproduce the EXACT scenario the user described with ALL their parameters. |
| read-spec-first | CRIT | Starting work on a SPEC/feature | Read the full SPEC document before implementing. Check `.workflow/specs/`. |
| minimal-changes | HIGH | Working on any task | Only make changes that were explicitly asked for. No "improvements" to surrounding code. |
| wait-after-question | HIGH | You asked the user a question | STOP and WAIT for the answer. Do not continue with tool calls in the same response. |
| listen-before-acting | CRIT | User starts explaining something | Read the ENTIRE message before taking ANY action. Summarize understanding first. |
| ask-before-retry | HIGH | Operation failed 1-2 times | After 2 failures, STOP and ask the user. Summarize findings before retrying. |
| debug-data-before-theory | HIGH | Investigating unexpected behavior | Examine actual data (logs, DB, API responses) BEFORE forming theories. |
| verify-full-flow | HIGH | Fixing a bug in a multi-step pipeline | Verify ALL downstream steps still work, not just the step you touched. |
| check-process-not-curl | HIGH | Checking if a server is running | Use `lsof -nP -iTCP:PORT -sTCP:LISTEN`. Never `curl` without `--connect-timeout 2 --max-time 3`. |
