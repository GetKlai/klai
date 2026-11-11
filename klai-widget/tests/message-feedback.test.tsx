/**
 * Visitor rating under an assistant answer: a click reaches the feedback
 * endpoint with the turn id and session token, and the control carries a
 * visible question so visitors can tell what the thumbs are for.
 */
import { cleanup, fireEvent, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageList } from "../src/components/MessageList";
import { initLabels } from "../src/i18n/labels";
import { setChatState } from "../src/store/chat";
import type { Message } from "../src/api/chat-stream";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TURN_ID = "0123456789abcdef0123456789abcdef";

function answer(): Message {
  return { role: "assistant", content: "Here is the answer.", turnId: TURN_ID };
}

describe("answer rating", () => {
  it("posts the turn id and rating with the session token on click", () => {
    initLabels("en");
    setChatState("messages", [answer()]);
    setChatState("sessionToken", "token-1");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    const { getByLabelText } = render(() => (
      <MessageList messages={[answer()]} isStreaming={false} error={null} />
    ));
    fireEvent.click(getByLabelText("Helpful"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/partner\/v1\/widget\/feedback$/);
    expect(init.headers.Authorization).toBe("Bearer token-1");
    expect(JSON.parse(init.body)).toEqual({ turn_id: TURN_ID, rating: "thumbsUp" });
  });

  it("shows a visible question next to the thumbs, in Dutch and English", () => {
    for (const [lang, question] of [
      ["en", "Was this answer helpful?"],
      ["nl", "Was dit antwoord nuttig?"],
    ] as const) {
      initLabels(lang);
      const { container, unmount } = render(() => (
        <MessageList messages={[answer()]} isStreaming={false} error={null} />
      ));
      const group = container.querySelector(".klai-feedback")!;
      expect(group.textContent).toContain(question);
      expect(group.getAttribute("aria-labelledby")).toBe(
        group.querySelector(".klai-feedback-label")!.id,
      );
      unmount();
    }
  });
});
