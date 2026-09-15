/**
 * Per-turn language signal.
 *
 * The backend answers a turn in whatever language the visitor's question
 * was in, but the widget's chrome (buttons, placeholder, labels) used to be
 * picked once at load and then fixed for the whole session: initLabels()
 * ran once, and the labels it produced lived in a plain module variable
 * Solid had no way to react to. This exercises the real wiring end to
 * end — a mocked SSE frame goes through streamChat, into ChatWindow's
 * onLanguage callback, into the (now reactive) label signal — and asserts
 * on the rendered DOM, not on the label function in isolation.
 */
import { cleanup, fireEvent, render, waitFor } from "@solidjs/testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWindow } from "../src/components/ChatWindow";
import { currentLocale, initLabels } from "../src/i18n/labels";
import { normalizeLanguage } from "../src/api/chat-stream";
import { setChatState } from "../src/store/chat";
import type { WidgetConfig } from "../src/api/widget-config";

const CONFIG: WidgetConfig = {
  title: "Help",
  welcome_message: "Hoi, waar kan ik je mee helpen?",
  css_variables: {},
  chat_endpoint: "https://api.example.test/partner/v1/widget/chat",
  session_token: "test-session-token",
  session_expires_at: new Date(Date.now() + 3600_000).toISOString(),
};

function sseResponse(frames: Record<string, unknown>[]): Response {
  const body =
    frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join("") + "data: [DONE]\n\n";
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function mockStream(frames: Record<string, unknown>[]): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(frames)));
}

function renderReadyWindow() {
  setChatState({
    config: CONFIG,
    sessionToken: CONFIG.session_token,
    widgetId: "widget-language-test",
    clientSessionId: "conv-language-test",
    messages: [],
    isStreaming: false,
    conversationStatus: "active",
    error: null,
    handoffActive: false,
    handoffConnecting: false,
    broadMode: false,
  });
  return render(() => (
    <ChatWindow title="Help" onClose={() => {}} inline manageHandoffStream={false} />
  ));
}

async function ask(container: HTMLElement, text: string): Promise<void> {
  const textarea = container.querySelector(".klai-textarea") as HTMLTextAreaElement;
  fireEvent.input(textarea, { target: { value: text } });
  fireEvent.click(container.querySelector(".klai-send-btn") as HTMLButtonElement);
}

function placeholder(container: HTMLElement): string {
  return (container.querySelector(".klai-textarea") as HTMLTextAreaElement).placeholder;
}

function lastAnswerText(container: HTMLElement): string | undefined {
  const answers = container.querySelectorAll(".klai-message--assistant");
  return answers[answers.length - 1]?.textContent?.trim();
}

beforeEach(() => {
  initLabels("nl");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("per-turn language switch", () => {
  it("switches the chrome to English mid-conversation, without a reload", async () => {
    mockStream([
      { choices: [{ delta: { language: "en" } }] },
      { choices: [{ delta: { content: "You bet, go ahead." } }] },
    ]);
    const { container } = renderReadyWindow();
    expect(placeholder(container)).toBe("Stel een vraag...");

    await ask(container, "Can we speak English?");

    await waitFor(() => expect(lastAnswerText(container)).toBe("You bet, go ahead."));
    expect(placeholder(container)).toBe("Ask a question...");
    expect(container.querySelector(".klai-send-btn")?.getAttribute("aria-label")).toBe("Send message");
  });

  it("stays Dutch for a conversation that never switches", async () => {
    mockStream([{ choices: [{ delta: { content: "Prima, hier is het antwoord." } }] }]);
    const { container } = renderReadyWindow();

    await ask(container, "Hoe reset ik mijn wachtwoord?");

    await waitFor(() => expect(lastAnswerText(container)).toBe("Prima, hier is het antwoord."));
    expect(placeholder(container)).toBe("Stel een vraag...");
  });

  it("keeps the switched language when a later turn carries no language field", async () => {
    mockStream([
      { choices: [{ delta: { language: "en" } }] },
      { choices: [{ delta: { content: "Sure." } }] },
    ]);
    const { container } = renderReadyWindow();
    await ask(container, "Switch to English please");
    await waitFor(() => expect(placeholder(container)).toBe("Ask a question..."));

    mockStream([{ choices: [{ delta: { content: "Still here." } }] }]);
    await ask(container, "Another question");

    await waitFor(() => expect(lastAnswerText(container)).toBe("Still here."));
    expect(placeholder(container)).toBe("Ask a question...");
  });

  it("ignores a language the widget has no label set for", async () => {
    mockStream([
      { choices: [{ delta: { language: "de" } }] },
      { choices: [{ delta: { content: "Hallo." } }] },
    ]);
    const { container } = renderReadyWindow();

    await ask(container, "Sprechen Sie Deutsch?");

    await waitFor(() => expect(lastAnswerText(container)).toBe("Hallo."));
    expect(placeholder(container)).toBe("Stel een vraag...");
  });

  it("leaves the externally loaded booking panel on the load-time locale", async () => {
    mockStream([
      { choices: [{ delta: { language: "en" } }] },
      { choices: [{ delta: { content: "You bet, go ahead." } }] },
    ]);
    const { container } = renderReadyWindow();

    await ask(container, "Can we speak English?");
    await waitFor(() => expect(placeholder(container)).toBe("Ask a question..."));

    // The Nerds panel frames a third-party page whose language is a product
    // decision of its own; it must not follow the conversation.
    expect(currentLocale()).toBe("nl");
  });

  it("switches from the non-streaming message.language shape just as well", async () => {
    mockStream([
      { choices: [{ message: { language: "en" } }] },
      { choices: [{ delta: { content: "Yes." } }] },
    ]);
    const { container } = renderReadyWindow();

    await ask(container, "Can I get an answer in English?");

    await waitFor(() => expect(lastAnswerText(container)).toBe("Yes."));
    expect(placeholder(container)).toBe("Ask a question...");
  });
});

describe("normalizeLanguage", () => {
  it("accepts only the codes the widget has a label set for", () => {
    expect(normalizeLanguage("nl")).toBe("nl");
    expect(normalizeLanguage("en")).toBe("en");
  });

  it("rejects unsupported codes, absence, and malformed values", () => {
    for (const raw of ["de", "fr", "pt", "es", "", undefined, null, 1, {}, []]) {
      expect(normalizeLanguage(raw)).toBeNull();
    }
  });
});
