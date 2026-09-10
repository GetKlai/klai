/**
 * The session-token cache for /partner/v1/widget-config.
 *
 * Every fetch of that endpoint mints a fresh session token server-side and
 * the mint path is rate-limited to 10/min per widget, so a visitor clicking
 * through help articles used to burn the customer site's whole budget on
 * reopens of one still-valid hour. These tests replay the reported flow:
 * open the chat, simulate a page reload (reset modules, keep localStorage),
 * open again — the mint must happen once, until the reuse window or the
 * token expiry passes, a conversation switch changes the session id (the
 * backend stamps it into the JWT as `jti`), or a chat call 401s.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const WIDGET_ID = "wgt_test";
const CONV = "conv-current";
const MINUTE = 60_000;

// Captures every fetchEventSource call so the 401 test can drive the real
// onopen/onerror/onmessage handlers from src/api/chat-stream.ts.
const fesCalls = vi.hoisted(() => ({
  opts: [] as Array<{
    headers: Record<string, string>;
    onopen: (response: Response) => Promise<void>;
    onerror: (error: unknown) => void;
    onmessage: (event: { data: string }) => void;
  }>,
}));

vi.mock("@microsoft/fetch-event-source", () => ({
  fetchEventSource: (_url: string, opts: unknown) => {
    fesCalls.opts.push(opts as (typeof fesCalls.opts)[number]);
    return Promise.resolve();
  },
}));

let mints = 0;
let tokenLifetimeMs = 60 * MINUTE;

function configResponse(): Response {
  mints += 1;
  return new Response(
    JSON.stringify({
      title: "Klai",
      welcome_message: "Hoi!",
      css_variables: {},
      chat_endpoint: "/partner/v1/widget-chat/completions",
      session_token: `tok-${mints}`,
      session_expires_at: new Date(Date.now() + tokenLifetimeMs).toISOString(),
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

// Everything in this file is microtasks and mocked promises; fake timers are
// here for the clock (expiry, reuse window), never for waiting. settle()
// drains the microtask chain.
async function settle(rounds = 25): Promise<void> {
  for (let i = 0; i < rounds; i += 1) {
    await vi.advanceTimersByTimeAsync(0);
  }
}

/** Fresh bootstrap (simulated reload: old DOM gone, modules re-imported,
 * localStorage untouched) plus the facade click that launches the chat. */
async function openChat(): Promise<void> {
  document.querySelectorAll("#klai-widget-root").forEach((node) => node.remove());
  vi.resetModules();
  await import("../src/main");
  await settle();

  const root = document.getElementById("klai-widget-root");
  expect(root).not.toBeNull();
  const bubble = root!.shadowRoot!.querySelector("button.klai-bubble");
  expect(bubble).not.toBeNull();
  (bubble as HTMLButtonElement).click();
  await settle();
}

describe("widget session-token cache", () => {
  beforeEach(() => {
    mints = 0;
    tokenLifetimeMs = 60 * MINUTE;
    fesCalls.opts.length = 0;
    vi.useFakeTimers();
    vi.resetModules();
    window.localStorage.clear();
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    const script = document.createElement("script");
    script.src = "https://my.getklai.com/widget/klai-chat.js";
    script.setAttribute("data-widget-id", WIDGET_ID);
    document.head.appendChild(script);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/partner/v1/widget-config")) return configResponse();
      throw new Error(`unexpected fetch: ${String(input)}`);
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("reuses the cached mint when the chat reopens after a reload", async () => {
    await openChat();
    expect(mints).toBe(1);

    await openChat();
    expect(mints).toBe(1);
  });

  it("disables cache reuse when the embed script sets data-fresh-config", async () => {
    document
      .querySelector("script[data-widget-id]")!
      .setAttribute("data-fresh-config", "true");

    await openChat();
    await openChat();
    expect(mints).toBe(2);
  });

  it("re-mints once the 15-minute reuse window passes", async () => {
    const { fetchWidgetConfig } = await import("../src/api/widget-config");
    const reused = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(reused.session_token).toBe("tok-1");

    // 14m59s after mint: still inside the window (token itself is good for an hour).
    await vi.advanceTimersByTimeAsync(15 * MINUTE - 1000);
    const hit = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(hit.session_token).toBe("tok-1");
    expect(mints).toBe(1);

    // 15m01s after mint: stale settings ceiling, re-mint and refresh the entry.
    await vi.advanceTimersByTimeAsync(2000);
    const miss = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(miss.session_token).toBe("tok-2");
    expect(mints).toBe(2);
  });

  it("stops reusing 60s before the token expiry, inside the reuse window", async () => {
    tokenLifetimeMs = 10 * MINUTE;
    const { fetchWidgetConfig } = await import("../src/api/widget-config");
    await fetchWidgetConfig(WIDGET_ID, { sessionId: CONV, reuseCachedSession: true });

    // 8m59s after mint = expiry − 61s: one second of usable life above the margin.
    await vi.advanceTimersByTimeAsync(9 * MINUTE - 1000);
    const hit = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(hit.session_token).toBe("tok-1");
    expect(mints).toBe(1);

    // 9m01s after mint = expiry − 59s: inside the 60s margin, must re-mint.
    await vi.advanceTimersByTimeAsync(2000);
    const miss = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(miss.session_token).toBe("tok-2");
    expect(mints).toBe(2);
  });

  it("never replays a token minted for another conversation", async () => {
    const { fetchWidgetConfig } = await import("../src/api/widget-config");
    await fetchWidgetConfig(WIDGET_ID, { sessionId: "conv-a", reuseCachedSession: true });
    expect(mints).toBe(1);

    // Conversation switch: ChatWindow mints straight for the new id, which
    // overwrites the single entry.
    await fetchWidgetConfig(WIDGET_ID, { sessionId: "conv-b" });
    expect(mints).toBe(2);

    // A reopen for the old conversation must not receive conv-b's token —
    // its jti is a different conversation's audit key.
    const a = await fetchWidgetConfig(WIDGET_ID, { sessionId: "conv-a", reuseCachedSession: true });
    expect(mints).toBe(3);
    expect(a.session_token).toBe("tok-3");

    // And conv-a's mint in turn overwrote conv-b: reopens of the active
    // conversation reuse, switches mint.
    const b1 = await fetchWidgetConfig(WIDGET_ID, { sessionId: "conv-b", reuseCachedSession: true });
    expect(mints).toBe(4);
    const b2 = await fetchWidgetConfig(WIDGET_ID, { sessionId: "conv-b", reuseCachedSession: true });
    expect(b2.session_token).toBe(b1.session_token);
    expect(mints).toBe(4);
  });

  it("re-mints once after a 401, hands the fresh token to the caller and refreshes the cache", async () => {
    const { fetchWidgetConfig, clearCachedWidgetSession } = await import(
      "../src/api/widget-config"
    );
    await fetchWidgetConfig(WIDGET_ID, { sessionId: CONV, reuseCachedSession: true });
    expect(mints).toBe(1); // tok-1 cached for CONV

    const { streamChat } = await import("../src/api/chat-stream");
    const errors: unknown[] = [];
    const refreshed: string[] = [];
    const turn = (token: string) =>
      streamChat({
        endpoint: "https://api.getklai.com/partner/v1/widget-chat/completions",
        token,
        widgetId: WIDGET_ID,
        sessionId: CONV,
        messages: [{ role: "user", content: "hallo" }],
        callbacks: {
          onToken: () => {},
          onDone: () => {},
          onError: (error) => errors.push(error),
          onTokenRefreshed: (fresh) => refreshed.push(fresh),
        },
      });

    const first = turn("tok-1");
    await settle();
    expect(fesCalls.opts[0]!.headers.Authorization).toBe("Bearer tok-1");

    // Mirror the library's contract: onopen rejects, the error goes to
    // onerror, and onerror rethrowing is what stops the library's retry.
    try {
      await fesCalls.opts[0]!.onopen(new Response("", { status: 401 }));
    } catch (error) {
      try {
        fesCalls.opts[0]!.onerror(error);
      } catch {
        /* expected rethrow */
      }
    }
    await settle();

    // The retried stream runs on a freshly minted token for the same
    // conversation…
    expect(fesCalls.opts[1]!.headers.Authorization).toBe("Bearer tok-2");
    await fesCalls.opts[1]!.onopen(new Response("", { status: 200 }));
    fesCalls.opts[1]!.onmessage({ data: "[DONE]" });
    await first;
    expect(errors).toEqual([]);
    // …and the caller is handed the fresh token (ChatWindow stores it).
    expect(refreshed).toEqual(["tok-2"]);

    // A second real turn on the fresh token streams without 401 and above
    // all without minting again.
    const second = turn(refreshed[0]!);
    await settle();
    expect(fesCalls.opts[2]!.headers.Authorization).toBe("Bearer tok-2");
    await fesCalls.opts[2]!.onopen(new Response("", { status: 200 }));
    fesCalls.opts[2]!.onmessage({ data: "[DONE]" });
    await second;
    expect(mints).toBe(2);

    // The 401ed token was evicted and the re-mint refreshed the cache: a
    // reload reuses tok-2.
    const reloaded = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(reloaded.session_token).toBe("tok-2");
    expect(mints).toBe(2);

    // A stale tab retrying with the rejected token must not wipe the newer
    // entry; clearing only removes when the cached token matches.
    clearCachedWidgetSession(WIDGET_ID, "tok-1");
    const survives = await fetchWidgetConfig(WIDGET_ID, {
      sessionId: CONV,
      reuseCachedSession: true,
    });
    expect(survives.session_token).toBe("tok-2");
    expect(mints).toBe(2);

    clearCachedWidgetSession(WIDGET_ID, "tok-2");
    await fetchWidgetConfig(WIDGET_ID, { sessionId: CONV, reuseCachedSession: true });
    expect(mints).toBe(3);
  });
});
