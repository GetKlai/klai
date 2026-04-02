---
paths:
  - "**/.workflow/specs/**"
  - "**/docs/specs/**"
  - "**/SPEC*.md"
---
# Process Pitfalls

> AI-assisted development workflow. Full descriptions — see process-rules.md for the compact always-loaded version.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [process-validate-before-code-change](#process-validate-before-code-change) | HIGH | Validate hypothesis with real data before writing code |
| [process-verify-completion-claims](#process-verify-completion-claims) | CRIT | Verify AI completion claims with `git diff --stat` |
| [process-server-restart-protocol](#process-server-restart-protocol) | CRIT | Never use `run_in_background` to start servers |
| [process-test-user-facing-not-imports](#process-test-user-facing-not-imports) | HIGH | Test actual user-facing functionality, not just imports |
| [process-debug-logging-first](#process-debug-logging-first) | HIGH | Add debug logging before implementing fixes |
| [process-trust-user-feedback](#process-trust-user-feedback) | CRIT | Reproduce exact user scenario when they say it's broken |
| [process-read-spec-first](#process-read-spec-first) | CRIT | Read full SPEC document before implementing anything |
| [process-minimal-changes](#process-minimal-changes) | HIGH | Only make changes explicitly asked for |
| [process-wait-after-question](#process-wait-after-question) | HIGH | Stop and wait after asking a question |
| [process-listen-before-acting](#process-listen-before-acting) | CRIT | Read entire message before taking any action |
| [process-ask-before-retry](#process-ask-before-retry) | HIGH | Ask user after 2 failures; don't auto-retry |
| [process-debug-data-before-theory](#process-debug-data-before-theory) | HIGH | Check actual data (logs, DB) before forming theories |
| [process-verify-full-flow](#process-verify-full-flow) | HIGH | Verify all downstream steps after fixing a bug |
| [process-check-process-not-curl](#process-check-process-not-curl) | HIGH | Use `lsof` to check ports; add timeouts to curl |
| [process-no-prompt-files](#process-no-prompt-files) | HIGH | Output prompts in chat, never write to markdown files |
| [process-spec-stub-deps-premature](#process-spec-stub-deps-premature) | HIGH | Comment out new deps at stub time; enable in `/run` |
| [process-test-cache-two-level-dispatch](#process-test-cache-two-level-dispatch) | MED | Dispatch by key prefix in multi-key cache mocks |
| [process-no-architecture-change-in-migration](#process-no-architecture-change-in-migration) | CRIT | During migrations, move services as-is — never consolidate or replace without explicit approval |
| [process-evidence-only-confidence](#process-evidence-only-confidence) | CRIT | Require observable evidence for confidence claims |
| [process-report-confidence](#process-report-confidence) | HIGH | End with `Confidence: [0-100] — [evidence]` |
| [process-adversarial-at-high-confidence](#process-adversarial-at-high-confidence) | HIGH | Bug-hunt at confidence >= 80 |
| [process-diagnose-before-fixing](#process-diagnose-before-fixing) | HIGH | Stop, read logs, one hypothesis, one fix |
| [process-verify-system-not-just-feature](#process-verify-system-not-just-feature) | HIGH | Verify entry point, not just tests |
| [process-search-all-case-variants](#process-search-all-case-variants) | HIGH | Search all case variants when renaming |
| [process-convention-change-blast-radius](#process-convention-change-blast-radius) | HIGH | Search entire codebase for consumers of changed defaults |

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

**Root cause:** AI agents pattern-match errors to likely causes and skip verification, leading to speculative multi-fix attempts that compound complexity.
**Source:** Multiple incidents where agents applied 3-4 "fixes" without checking logs first.

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

**Root cause:** LLMs can hallucinate detailed completion metrics (line counts, method counts) without having done the actual work — preparation steps feel like implementation.
**Source:** Vexa Conductor LEARNINGS.md — evaluator caught 4+ false completion claims from dev agent.

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

**Root cause:** Background processes survive the session, bind stale code to ports, and mask startup failures.
**Source:** Incident where portal-api had two processes on port 8010, serving different code versions.

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

**Root cause:** `python -c "import module"` tests syntax, not behavior — agents confuse import success with functional success.
**Source:** Vexa G1 gotcha — "8/8 Playwright tests passed, dashboard unreachable."

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

**Root cause:** Without seeing the actual payload, agents form theories from code structure rather than runtime reality, leading to fixes for the wrong problem.
**Source:** Repeated incidents with external API integrations where the actual response differed from assumed format.

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

**Root cause:** Agents test with different parameters than the user reported, declare "fixed", and miss the actual failure condition.
**Source:** Multiple incidents where user reported broken feature, agent's tests passed with different inputs.

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

**Root cause:** Agents start implementing based on the task title alone, missing critical constraints, exclusions, and architectural decisions documented in the SPEC.
**Source:** Several SPEC implementations where scope was wrong because the SPEC wasn't fully read.

---

## process-minimal-changes

**Severity:** HIGH

**Trigger:** Working on any task

Only make the changes that were explicitly asked for. Do not "improve" surrounding code, add comments, refactor adjacent functions, or add features that weren't requested.

**Rule (from CLAUDE.md):** Minimal changes: only what was asked.

**Why:** Unrequested changes introduce untested risk and make diffs harder to review. A bug fix should touch only the bug. A feature should touch only that feature.

**Root cause:** Agents have a strong tendency to "improve" adjacent code, add comments, and refactor nearby functions — each change adds untested risk.
**Source:** Anthropic guidance + repeated observation of agents modifying files they didn't need to touch.

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

**Root cause:** The agent continues tool calls in the same response after asking a question, effectively ignoring the user's answer before it arrives.
**Source:** Observation — agents asking "Should I...?" and immediately doing it in the same turn.

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

**Root cause:** Agents act on the first sentence and miss critical context in the rest of the message, leading to wasted work on the wrong interpretation.
**Source:** Observation — agents starting tool calls after reading the first line of a multi-paragraph request.

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

**Root cause:** Repeated failed retries fill context with noise and waste time — the user often has key information that would change the approach entirely.
**Source:** Observation — agents retrying the same failing approach 5+ times without asking for help.

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

**Root cause:** Code review alone produces theories that may not match runtime reality — actual data (logs, DB state) reveals the ground truth.
**Source:** Observation — agents proposing multiple theoretical explanations without checking any actual data.

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

**Root cause:** A fix in step 3 of a pipeline may break step 4 — agents verify only the step they touched and declare "fixed."
**Source:** Incident where a connector sync fix broke the downstream indexing step.

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

**Root cause:** `curl` without timeouts hangs indefinitely against a down server, blocking the entire agent session.
**Source:** Incident where agent session hung for minutes waiting for curl against a stopped service.

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

**Root cause:** Agents write audit instructions and prompts to .md files that are single-use workflow artifacts, cluttering the repo.
**Source:** Observation — agents creating files like LOGGING_AUDIT_PROMPT.md.

---

## process-spec-stub-deps-premature

**Severity:** HIGH

**Trigger:** A spec agent generates stubs or scaffold code for a SPEC that introduces new third-party dependencies

When a spec agent writes stub files as part of SPEC generation, it may add new third-party packages to `requirements.txt`. This causes CI to fail at build time when the new package has a version conflict with existing pinned dependencies — before any implementation work has even begun.

**What happened:** During SPEC-KB-011, manager-spec added `graphiti-core[falkordb]>=0.28,<0.30` to `requirements.txt`. This caused CI failure because `graphiti-core 0.28` requires `pydantic>=2.11.5` but the service was pinned to `pydantic==2.10.3`.

**Why it happens:**
Stubs are meant to define the interface, not execute it. The real dependency installation happens in `/run` (the implementation phase), not during SPEC generation. Adding live dependencies at stub-time breaks CI before anyone has reviewed the dependency constraints.

**Prevention:**
1. Comment out new third-party dependencies in `requirements.txt` when writing stubs: `# graphiti-core[falkordb]>=0.28,<0.30  # TODO: uncomment in /run`
2. Guard stub imports with `try/except ImportError` so the module loads even without the dependency installed
3. The implementation agent (`/run`) is responsible for resolving version constraints and enabling the real dependency

```python
# Correct stub import guard
try:
    from graphiti_core import Graphiti
except ImportError:
    Graphiti = None  # type: ignore[assignment,misc]
```

**See also:** `pitfalls/devops.md` — CI verification after push

**Root cause:** Spec agents add real dependencies at stub time, before version constraints have been resolved, breaking CI.
**Source:** SPEC-KB-011 — graphiti-core pydantic version conflict broke CI during spec generation.

---

## process-test-cache-two-level-dispatch

**Severity:** MEDIUM

**Trigger:** Writing tests for a hook or service that uses a multi-key cache (e.g. version pointer + feature data)

When a cache uses multiple key prefixes (e.g. `kb_ver:{id}` → version string, `kb_feature:{id}:{ver}` → feature dict), a test helper that returns the same value for all keys will cause the code under test to treat the version string as the feature dict (KeyError or type error).

**Pattern — key-prefix dispatch in `AsyncMock.side_effect`:**
```python
async def _get(key: str) -> object:
    if key.startswith("kb_ver:"):
        return "0"           # version pointer
    if key.startswith("kb_feature:"):
        return feat_dict     # full feature dict
    return None              # cache miss
cache.async_get_cache = AsyncMock(side_effect=_get)
```

**Rule:** When testing two-level caches, always dispatch by key prefix in the mock, not by a single return value.

**Root cause:** A single return value mock doesn't distinguish between cache key prefixes, causing the code to treat a version string as a feature dict.
**Source:** SPEC-KB-013 — two-level version cache mock returned same value for all keys.

---

## process-no-architecture-change-in-migration

**Severity:** CRIT

**Trigger:** Migrating services from one server to another (e.g. moving GPU workloads from core-01 to gpu-01)

During a migration, move services **exactly as they are**. Never consolidate, replace, or redesign services as part of the same task — even if it seems like an obvious improvement.

**What happened:** During SPEC-GPU-001, an agent replaced two separate services (TEI for embeddings + Infinity for reranking) with a single consolidated Infinity instance. This was done without explicit user approval and turned out to be architecturally wrong: Infinity has a known GPU memory leak (issue #517) and no Prometheus metrics, making it unsuitable as the sole embedding service. The decision was discovered by the user only after the fact.

**What NOT to do:**
- Replace `tei` + `infinity-reranker` with a single `infinity` instance during a move
- Change API formats, consolidate services, or swap implementations mid-migration
- Mark a migration SPEC as complete when architectural decisions were made unilaterally

**What to do:**
1. Move services to the new server with identical configuration
2. Verify everything works identically on the new server
3. Only then propose architectural improvements as a **separate, explicitly approved task**

**Rule:** A migration task has one goal — same services, different server. Any architectural change requires its own SPEC and explicit user approval.

**Root cause:** Agents see migration as an opportunity to "improve" architecture — consolidating services or replacing implementations without approval.
**Source:** SPEC-GPU-001 — agent replaced TEI + Infinity with single Infinity instance during migration.

---

## process-evidence-only-confidence

**Severity:** CRIT

**Trigger:** Reporting confidence on task completion

Completion claims require observable evidence: test output, curl response, log output, browser verification. "Code looks correct," "should work," and "reviewed the code" score zero confidence — only verifiable signals count.

**Root cause:** LLMs are 5.5x more likely to be confidently wrong than unsure about something right (Kaddour et al. 2026). Without requiring evidence, agents report high confidence based on code review alone.
**Source:** Vexa Conductor — "code looks correct" was the #1 false confidence signal. Kaddour et al. 2026 research.

---

## process-report-confidence

**Severity:** HIGH

**Trigger:** Ending a task or stopping a session

End completion messages with `Confidence: [0-100] — [evidence summary]`. The stop hook enforces this mechanically — without a confidence number backed by observable evidence, the session cannot end.

**Root cause:** Without a structured format, agents declare "done" without quantifying uncertainty or providing evidence.
**Source:** Vexa Conductor stop hook pattern — mechanical enforcement achieves ~100% compliance vs ~70% for prompt instructions.

---

## process-adversarial-at-high-confidence

**Severity:** HIGH

**Trigger:** Reporting confidence >= 80

At confidence >= 80, ask yourself "what bugs can I find in what I just did?" Frame as bug-hunting, not confirmation. The question "is this correct?" triggers confirmation bias; adversarial framing reduces overconfidence by ~15 percentage points.

**Root cause:** Agents at high confidence skip self-review — the adversarial frame forces a second look that catches missed issues.
**Source:** Kaddour et al. 2026 — adversarial framing study. Vexa Conductor adversarial check pattern.

---

## process-diagnose-before-fixing

**Severity:** HIGH

**Trigger:** Something breaks or an error appears

When something breaks: stop, read logs first, form one hypothesis, try one fix. If that fails, form a new hypothesis from the new evidence. After two failed fixes, escalate to the user.

**What NOT to do:**
- Try random fixes hoping something sticks
- Apply multiple patches without diagnosing root cause
- Keep fixing without re-reading logs after each attempt

**Root cause:** Without a diagnosis step, agents enter a "flailing" pattern — trying 4+ fixes without identifying root cause, all of which fail.
**Source:** Vexa G2/G3 gotcha — agent tried 4 fixes without root cause analysis, all failed.

---

## process-verify-system-not-just-feature

**Severity:** HIGH

**Trigger:** Completing code changes

After code changes, verify the system works end-to-end — not just the feature you touched. Tests passing doesn't mean the entry point (API endpoint, dashboard page, service) is actually reachable by the user.

**What to do:**
1. Run the test suite (feature-level verification)
2. Check the entry point: API responds, page loads, service is healthy
3. If it's a UI change, verify the user flow that triggers it

**Root cause:** Unit tests verify isolated behavior but miss system-level issues like misconfigured routes, broken middleware, or service startup failures.
**Source:** Vexa G1/G8 gotcha — "8/8 Playwright tests passed, dashboard unreachable."

---

## process-search-all-case-variants

**Severity:** HIGH

**Trigger:** Renaming a symbol, constant, or convention across the codebase

When renaming across the codebase, search all case variants: kebab-case, snake_case, camelCase, PascalCase, SCREAMING_SNAKE. Missing one variant breaks silently — often only discovered in production.

**Example:** Renaming "bot-manager" requires checking: `bot-manager`, `bot_manager`, `botManager`, `BotManager`, `BOT_MANAGER`.

**Root cause:** Agents grep for one case variant and assume full coverage. Different files and contexts use different conventions for the same concept.
**Source:** Vexa G4 gotcha — missed camelCase variant in a 107-file grep, causing a silent breakage.

---

## process-convention-change-blast-radius

**Severity:** HIGH

**Trigger:** Changing a default value, naming convention, or shared constant

When changing a default value or naming convention, search the entire codebase for consumers — not just the files in your plan. Every file that hardcodes the old value is a consumer you must update.

**What to do:**
1. Before changing: grep the entire codebase for the old value
2. List all consumers, even in unexpected locations (tests, configs, docs, scripts)
3. Update all consumers or verify they don't need updating
4. If consumers span more than 5 files, flag this as a high-risk change

**Root cause:** Default values have unbounded blast radius — they're used across boundaries agents don't consider (test fixtures, config files, documentation, other services).
**Source:** Vexa G5 gotcha — convention change caused 14 stale references in 9 unplanned files.

---

## See Also

- [pitfalls/platform.md](platform.md) - Platform-specific mistakes (LiteLLM, LibreChat, Caddy)
- [pitfalls/infrastructure.md](infrastructure.md) - Infrastructure mistakes (SOPS, env vars, Hetzner)
- [pitfalls/devops.md](devops.md) - Deployment and Docker mistakes
- [patterns/testing.md](../patterns/testing.md) - Standard Playwright testing workflow
