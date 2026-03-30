# Vexa Leave Detection Pitfalls

> Vexa meeting bot — Google Meet leave/end detection via Playwright DOM scraping.
> All findings confirmed by live testing on a real Google Meet session.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [vexa-meet-end-browser-crash](#vexa-meet-end-browser-crash) | CRIT | Meeting end = Playwright "Target crashed"; catch separately |
| [vexa-dom-freeze-on-participant-leave](#vexa-dom-freeze-on-participant-leave) | HIGH | After leave, DOM freezes; use timeout-based detection |
| [vexa-everyoneLeftTimeout-wrong-field](#vexa-everyoneLeftTimeout-wrong-field) | HIGH | Config field is `everyoneLeftTimeout`, not the class name |
| [vexa-six-leave-mechanisms-status](#vexa-six-leave-mechanisms-status) | HIGH | Implement all 6 leave/end detection paths |
| [vexa-upstream-issue-190-fake-participants](#vexa-upstream-issue-190-fake-participants) | MED | Fake participant count bug still open upstream |
| [vexa-issue-189-video-blocking-regression](#vexa-issue-189-video-blocking-regression) | HIGH | Video track blocking regression in upstream |
| [vexa-playwright-dom-scraping-fragility](#vexa-playwright-dom-scraping-fragility) | MED | DOM scraping is fragile; prefer event-based detection |
| [vexa-what-still-needs-fixing](#vexa-what-still-needs-fixing) | HIGH | Known open issues not yet fixed |

---

## vexa-meet-end-browser-crash

**Severity:** CRIT

**Trigger:** Google Meet meeting ends (host leaves or meeting is terminated)

When a Google Meet session ends, Playwright's Chromium browser crashes with a **"Target crashed"** error. This happens at the WebRTC/browser layer — before any JavaScript selector polling has a chance to run.

The crash is caused by WebRTC connection teardown triggering a Chromium crash. None of Vexa's standard leave-detection mechanisms can intercept this, because:

- Selector polling (removal.ts) runs in a `setInterval` — the crash kills the process before the next tick
- DOM element counts (data-participant-id) never drop because the page is frozen, not updated
- `beforeunload` and `visibilitychange` events do not reliably fire when the browser crashes

**The only reliable fix** is to attach a Playwright-level crash handler before joining the meeting:

```typescript
page.on('crash', () => { /* trigger bot shutdown */ });
page.on('close', () => { /* trigger bot shutdown */ });
```

These handlers fire even when the page crashes. Selector-based detection cannot substitute for them.

**Source:** Confirmed by live Google Meet testing; the fix was implemented in `removal.js`.

---

## vexa-dom-freeze-on-participant-leave

**Severity:** HIGH

**Trigger:** Counting `data-participant-id` elements to detect when all participants have left

Google Meet's DOM does **not** remove participant tile elements when participants leave. The DOM is frozen or lazily updated. A count of `[data-participant-id]` elements will remain at 2 (or more) even after the remote participant has disconnected.

This breaks Vexa's "left alone" timeout in `recording.ts`: the participant count never drops to 1 (bot only), so the timeout never triggers.

**Wrong assumption:**
```
participants = page.$$('[data-participant-id]').length
// Expected: drops to 1 when remote leaves → triggers timeout
// Actual:   stays at 2 indefinitely, DOM is not updated
```

**Implication:** The data-participant-id approach (our patch to fix issue #190) correctly avoids counting UI chrome as fake participants, but does not solve leave detection. It is a partial fix only.

**Source:** Confirmed by live Google Meet testing.

---

## vexa-everyoneLeftTimeout-wrong-field

**Severity:** HIGH

**Trigger:** Configuring the bot's alone-detection timeout via `process.py` / API call

Vexa's `everyoneLeftTimeoutSeconds` field does **not exist** in the codebase. The actual field is `everyoneLeftTimeout`, and its value is in **milliseconds** (not seconds). Vexa upstream PR #172 (already merged) changed the field name and unit.

Using the old field name causes Vexa to read `undefined`, fall back to its hardcoded default (10 seconds), and silently ignore the configured value.

**Wrong (our current patch in process.py):**
```python
"everyoneLeftTimeoutSeconds": 10
```

**Correct (matching PR #172):**
```python
"everyoneLeftTimeout": 30000   # value in milliseconds
```

**Fix:** Update `process.py` to use `everyoneLeftTimeout` with a millisecond value before relying on alone-timeout behavior in any environment running a post-PR-#172 Vexa image.

**Source:** Vexa upstream PR #172; confirmed by reading Vexa source.

---

## vexa-six-leave-mechanisms-status

**Severity:** HIGH

**Trigger:** Debugging why the bot does not exit when a meeting ends

Vexa has six leave-detection mechanisms. Their actual status (as of live testing) is:

| # | Mechanism | File | Status | Reason |
|---|-----------|------|--------|--------|
| 1 | Removal Monitor (selector polling) | `removal.ts` | BROKEN | Page crashes before selectors can be checked |
| 2 | Left-Alone Timeout (runtime) | `recording.ts` | BROKEN | DOM freezes, participant count never drops |
| 3 | Startup-Alone Timeout | `recording.ts` | BROKEN | Same DOM freeze issue as #2 |
| 4 | Waiting Room Timeout | `recording.ts` | WORKS | Detects failure to join, not end-of-meeting |
| 5 | `beforeunload` event | injected JS | UNRELIABLE | Does not fire on Chromium crash |
| 6 | `visibilitychange` hidden | injected JS | UNRELIABLE | Does not fire on Chromium crash |

Mechanisms 1–3 and 5–6 all fail on crash. Only mechanism 4 (waiting room) works reliably, but it does not detect meeting end.

The `page.on('crash')` handler (see `vexa-meet-end-browser-crash` above) is the only reliable supplement to this set.

**Source:** Confirmed by live Google Meet testing and reading Vexa source.

---

## vexa-upstream-issue-190-fake-participants

**Severity:** MEDIUM

**Trigger:** Bot exits immediately after joining because it thinks it is alone

Vexa's `extractParticipantsFromMain()` counts **all** elements matching a broad selector, including UI chrome (control bar, captions panel, etc.) as fake "participants". This causes the participant count to be inflated, or worse, triggers a premature alone-timeout if the count drops.

**Our patch:** Use `[data-participant-id]` attribute as the selector instead of the broad selector. This is correct and avoids the chrome-counting bug. However, it does not fix leave detection (see `vexa-dom-freeze-on-participant-leave`).

**Upstream status:** Reported as Vexa issue #190. Not yet fixed upstream as of the time of writing.

---

## vexa-issue-189-video-blocking-regression

**Severity:** HIGH

**Trigger:** Running a locally-built Vexa image (not the published Docker image)

Vexa's video-blocking patch (intended to reduce CPU/bandwidth) contains a regression: it causes the bot to **exit when participants join**, not when they leave. This is the opposite of the intended behavior.

**Affected environment:** Only locally-built images. The published `ghcr.io/vexa-io/vexa` image does not exhibit this regression.

**Mitigation:** Use the published image unless there is a specific reason to build locally. If a local build is required, audit the video-blocking patch before deploying.

**Upstream status:** Reported as Vexa issue #189.

---

## vexa-playwright-dom-scraping-fragility

**Severity:** MEDIUM

**Trigger:** Any time Vexa's leave detection is relied upon in production

Vexa detects meeting state by scraping Google Meet's DOM using Playwright. This approach is fundamentally fragile:

- Google Meet is a React SPA with no stable public DOM API. Google does not guarantee selector stability across UI updates.
- DOM elements do not reliably appear or disappear on meeting state changes (see `vexa-dom-freeze-on-participant-leave`).
- The Chromium browser can crash when WebRTC connections are torn down, killing all selector-based detection.
- Any Google Meet UI update can silently break Vexa's selectors.

**More robust alternative:** Detect leave via WebRTC connection state events at the network layer, not the UI layer:

```javascript
// RTCPeerConnection.onconnectionstatechange fires reliably on disconnect
// Values: 'new' | 'connecting' | 'connected' | 'disconnected' | 'failed' | 'closed'
peerConnection.onconnectionstatechange = () => {
  if (['disconnected', 'failed', 'closed'].includes(peerConnection.connectionState)) {
    // meeting has ended
  }
};
```

WebRTC events are at the protocol layer and are not affected by DOM structure or React re-renders.

**Recommendation:** If contributing a fix upstream to Vexa, consider proposing a WebRTC-based detection path alongside the `page.on('crash')` handler as a more durable long-term solution.

---

## vexa-what-still-needs-fixing

**Severity:** HIGH

**Trigger:** Checklist before considering Vexa leave detection production-ready

Open items as of last investigation:

1. **`process.py` field name** — Change `everyoneLeftTimeoutSeconds: 10` to `everyoneLeftTimeout: <ms>` to match post-PR-#172 Vexa. Without this, the alone-timeout configuration is silently ignored.

2. **Upstream PR for `page.on('crash')`** — The crash handler fix (`removal.js`) solves the most critical failure mode. Consider opening a PR against the Vexa upstream repo to benefit the wider community and avoid re-patching on image updates.

3. **DOM freeze leave detection** — No selector-based approach can work around the frozen DOM. The `page.on('crash')` handler covers the "meeting ends" case. The "all participants leave but meeting stays open" case may still be undetected; validate in testing.

4. **Issue #189 video-blocking regression** — Do not build Vexa locally until this is fixed upstream, or audit and patch the video-blocking code before building.
