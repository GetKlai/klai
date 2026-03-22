# Process Pitfalls

> AI-assisted development workflow. Universal rules for every session.

---

## process-validate-before-code-change

**Severity:** HIGH

**Trigger:** Fixing a bug or changing code based on an error message

Before making code changes to fix a bug, validate the hypothesis with actual tests first.

**What to do:**
1. Form a hypothesis about the cause
2. Validate with real data (check logs, query the database, call the API endpoint)
3. Confirm the validation proves the hypothesis
4. Only then make code changes
5. Test again to confirm the fix works

**Why it matters:** Making multiple "fixes" based on assumptions wastes time and adds complexity. One root cause = one fix, identified from real data.

---

## process-verify-completion-claims

**Severity:** CRIT

**Trigger:** AI reports a task complete with detailed metrics (line counts, methods, files changed)

AI can hallucinate task completion by preparing for work but not executing it, then reporting fabricated details.

**Hallucination pattern:**
1. AI prepares for work (adds imports, creates skeleton files)
2. Reports entire task as complete with detailed metrics
3. Writes elaborate commit message about work NOT performed
4. Tests may pass if preparation steps work

**Detection:**
```bash
git diff --stat HEAD~1     # What actually changed?
git show HEAD --stat       # What was in that commit?
wc -l [target-file]        # Actual line count vs claimed
```

**Red flags:**
- Extremely detailed metrics without showing the actual work
- Quick completion of large, complex tasks
- All tests pass but metrics don't match expectations

---

## process-server-restart-protocol

**Severity:** CRIT

**Trigger:** Needing to restart a service or server

NEVER use `run_in_background=true` with the Bash tool to start servers. This creates zombie processes with stale code.

**Why it's wrong:**
- Creates background processes that survive the session
- Multiple processes can bind to the same port
- New code changes don't affect already-running processes
- No clean way to kill background processes

**Correct approach:**
- Use the project's restart script or `docker compose restart [service]`
- Always verify the service is responding after restart
- Check logs to confirm the new code is actually running

---

## process-test-user-facing-not-imports

**Severity:** HIGH

**Trigger:** Completing a migration, refactor, or bugfix

After code changes, test the actual user-facing functionality, not just that a module can be imported.

**The trap:**
```bash
# This only proves Python can parse the file:
python -c "from app.service import MyService"

# This proves the actual feature works:
curl -X POST http://localhost:8000/api/endpoint
```

**Rule:** Test what the user actually uses. A migration is not done until the endpoint or feature it affects is tested end-to-end.

---

## process-debug-logging-first

**Severity:** HIGH

**Trigger:** Investigating an API error or unexpected behavior that doesn't reproduce locally

When investigating external API or integration errors, add debug logging FIRST to see actual data before implementing fixes.

**What NOT to do:**
- Implement multiple complex "fixes" based on assumptions
- Claim "it's fixed" multiple times before seeing the actual payload

**What to do:**
1. Add logging to see the actual request/response data
2. Look at the ACTUAL failing payload before writing a fix
3. One root cause identified from real data → one targeted fix

---

## process-trust-user-feedback

**Severity:** CRIT

**Trigger:** User reports functionality is broken, while AI's own tests pass

When a user says something is broken, stop all other testing and investigate the exact scenario the user described.

**What NOT to do:**
- Test related but different functionality and claim "fixed"
- Test with different parameters than the user reported
- Keep claiming success when the user explicitly says it's still broken

**What to do:**
1. Ask: "What exactly fails? What error do you see? What URL/parameters?"
2. Reproduce the exact scenario with ALL parameters the user mentioned
3. Never claim success until the user confirms it works

---

## process-read-spec-first

**Severity:** CRIT

**Trigger:** Starting work on any SPEC or feature task

ALWAYS read the full SPEC document before implementing anything. The SPEC contains critical decisions about scope, approach, and constraints.

**What to do:**
1. Check `.workflow/specs/` for the relevant SPEC
2. Read the entire document, not just the title or first section
3. Note specific requirements, exclusions, and acceptance criteria
4. If anything is unclear, ask before assuming

---

## process-minimal-changes

**Severity:** HIGH

**Trigger:** Working on any task

Only make the changes that were explicitly asked for. Do not "improve" surrounding code, add comments, refactor adjacent functions, or add features that weren't requested.

**Rule (from CLAUDE.md):** Minimal changes: only what was asked.

**Why:** Unrequested changes introduce untested risk and make diffs harder to review. A bug fix should touch only the bug. A feature should touch only that feature.

---

## process-wait-after-question

**Severity:** HIGH

**Trigger:** You asked the user a question

After asking a question, STOP and WAIT for the answer. Do not continue with other actions in the same response.

**What NOT to do:**
- Ask a question and immediately continue with tool calls
- Ask "Should I...?" and then do it anyway
- Ask multiple questions and then start implementing

**Pattern:**
```
❌ Wrong: "Should I update the database? Let me also check the API..." [continues]
✅ Right: "Should I update the database?" [STOPS]
```

---

## process-listen-before-acting

**Severity:** CRIT

**Trigger:** User starts explaining a problem, requirement, or context

Read the user's ENTIRE explanation before taking ANY action. First sentence is never enough.

**What to do:**
1. Read the complete message
2. Summarize your understanding: "So the problem is X and you want Y — correct?"
3. Only then start investigating or planning

**What NOT to do:**
- Hear the first sentence, make an assumption, start running tools
- Act on partial context, then have to course-correct

**Why:** Missing context mid-explanation leads to wrong approaches, wasted time, and loss of trust.

---

## process-ask-before-retry

**Severity:** HIGH

**Trigger:** An operation failed 1-2 times and you are about to try again

After 2 failed attempts, STOP and ask the user before retrying.

**What to do:**
1. First failure: analyze what went wrong
2. Second failure: summarize findings, ask "Want me to try again? Here's what I found: ..."
3. Never start long-running retries without explicit permission

**Why:** Repeated failed attempts fill context with noise and waste time. The user may have key information that changes the approach entirely.

---

## process-debug-data-before-theory

**Severity:** HIGH

**Trigger:** Investigating unexpected behavior or a bug

Always examine actual data (logs, DB state, API responses) BEFORE forming theories about what's wrong.

**Correct pattern:**
```
DB/log query → see actual values → form hypothesis → test hypothesis
```

**Anti-pattern:**
```
Code review → hypothesis → more code review → untested explanation
```

**What NOT to do:**
- Say "this is probably because..." before you've seen the actual values
- Offer multiple theoretical explanations without checking real data first
- Make code changes based on what the code *should* do, not what it *actually* does

---

## process-verify-full-flow

**Severity:** HIGH

**Trigger:** Fixing a bug that involves multiple steps or a pipeline

When fixing a bug in a multi-step flow, verify ALL downstream steps still work — not just the step you touched.

**Before declaring "fixed":**
1. What does the fix change?
2. What are the downstream consumers of that change?
3. Does each consumer handle the new state correctly?

**Why:** Each "fix" can expose a new broken step downstream. A bug fix that only fixes one step in a five-step flow is still broken.

---

## process-check-process-not-curl

**Severity:** HIGH

**Trigger:** Checking whether a local server or service is running

Use `lsof` to check if a process is listening on a port. Never use `curl` without explicit timeouts — curl blocks indefinitely if the server is down or not yet ready.

**What NOT to do:**
```bash
curl http://localhost:8010/health   # hangs forever if server is down
```

**What to do:**
```bash
# Check if process is listening — instant, never blocks
lsof -nP -iTCP:8010 -sTCP:LISTEN

# If you need curl, always set timeouts
curl --connect-timeout 2 --max-time 3 -s http://localhost:8010/health
```

**Also:** Before starting a server, always check with `lsof` first. The process may already be running.

---

## process-no-prompt-files

**Severity:** HIGH

**Trigger:** Writing a prompt, audit template, or instruction for a follow-up task

NEVER write prompts or audit instructions to MD files. Always output them directly in the session conversation.

**What NOT to do:**
- Create `LOGGING_AUDIT_PROMPT.md` or similar files with task instructions
- Write prompts to disk that are only meant to guide the next AI action

**What to do:**
- Output the prompt/audit template directly in the chat as a message
- Let the user copy it if they want to persist it

**Why:** Prompts are ephemeral workflow artifacts, not project documentation. Writing them to files clutters the repo with single-use content that quickly becomes stale.

---

## See Also

- [pitfalls/platform.md](platform.md) - Platform-specific mistakes (LiteLLM, LibreChat, Caddy)
- [pitfalls/infrastructure.md](infrastructure.md) - Infrastructure mistakes (SOPS, env vars, Hetzner)
- [pitfalls/devops.md](devops.md) - Deployment and Docker mistakes
- [patterns/testing.md](../patterns/testing.md) - Standard Playwright testing workflow
