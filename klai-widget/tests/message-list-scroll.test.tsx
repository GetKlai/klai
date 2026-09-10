import { cleanup, render } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, describe, expect, it } from "vitest";

import { MessageList } from "../src/components/MessageList";

afterEach(cleanup);

describe("message list scrolling", () => {
  it("reveals the typing indicator when a later turn starts", async () => {
    const [isStreaming, setIsStreaming] = createSignal(false);
    const [messages, setMessages] = createSignal([
      { role: "assistant" as const, content: "A long earlier answer" },
    ]);
    const { container } = render(() => (
      <MessageList
        messages={messages()}
        isStreaming={isStreaming()}
        error={null}
      />
    ));
    const list = container.querySelector<HTMLElement>(".klai-messages")!;
    Object.defineProperty(list, "scrollHeight", { configurable: true, value: 600 });
    list.scrollTop = 100;

    setIsStreaming(true);
    await Promise.resolve();

    expect(container.querySelector(".klai-typing")).not.toBeNull();
    expect(list.scrollTop).toBe(600);

    list.scrollTop = 200;
    setMessages([{ role: "assistant", content: "A longer streamed answer" }]);
    await Promise.resolve();
    expect(list.scrollTop).toBe(200);
  });
});
