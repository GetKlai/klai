/**
 * Sources / agent-activity disclosure copy.
 *
 * These five strings used to be hardcoded Dutch inside MessageList.tsx, so
 * an English-speaking conversation still showed "Bronnen" and "Agent
 * activiteit" under an assistant answer. They now live in labels.ts next to
 * everything else t() renders, with a singular/plural split where zero
 * counts as plural in both languages.
 */
import { cleanup, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it } from "vitest";

import { MessageList } from "../src/components/MessageList";
import { activityCountLabel, answerBasedOnSourcesLabel, initLabels, sourceCountLabel } from "../src/i18n/labels";
import { setChatState } from "../src/store/chat";
import type { Message } from "../src/api/chat-stream";
import type { WidgetConfig } from "../src/api/widget-config";

afterEach(cleanup);

const CONFIG: WidgetConfig = {
  title: "Help",
  welcome_message: "Hi",
  css_variables: {},
  chat_endpoint: "https://api.example.test",
  session_token: "test-token",
  session_expires_at: new Date().toISOString(),
  show_meta: true,
};

function answer(overrides: Partial<Message> = {}): Message {
  return { role: "assistant", content: "Here is the answer.", ...overrides };
}

describe("source and activity disclosure, English", () => {
  it("renders English titles and the plural count for two sources", () => {
    initLabels("en");
    setChatState("config", CONFIG);

    const twoSources = answer({
      sources: [
        { label: "1", title: "Doc one", url: "https://example.com/1" },
        { label: "2", title: "Doc two", url: "https://example.com/2" },
      ],
      activity: [{ step: "search", label: "Searching" }],
    });
    const { container } = render(() => (
      <MessageList messages={[twoSources]} isStreaming={false} error={null} />
    ));

    const sourcesBlock = container.querySelector(".klai-disclosure--sources")!;
    expect(sourcesBlock.getAttribute("aria-label")).toBe("Sources");
    expect(sourcesBlock.querySelector(".klai-disclosure-title")?.textContent).toBe("Sources");
    expect(sourcesBlock.querySelector(".klai-disclosure-count")?.textContent).toBe("2 sources");

    const activityBlock = container.querySelector(".klai-disclosure--activity")!;
    expect(activityBlock.getAttribute("aria-label")).toBe("Agent activity");
    expect(activityBlock.querySelector(".klai-disclosure-title")?.textContent).toBe("Agent activity");
    expect(activityBlock.querySelector(".klai-disclosure-count")?.textContent).toBe("1 step");

    expect(container.querySelector(".klai-meta")?.textContent).toBe(
      "Answer based on 2 sources from the knowledge base.",
    );
  });

  it("uses the singular for exactly one", () => {
    initLabels("en");
    setChatState("config", CONFIG);
    const oneSource = answer({
      sources: [{ label: "1", title: "Doc", url: "https://example.com" }],
    });
    const { container } = render(() => (
      <MessageList messages={[oneSource]} isStreaming={false} error={null} />
    ));

    expect(container.querySelector(".klai-disclosure--sources .klai-disclosure-count")?.textContent).toBe(
      "1 source",
    );
    expect(container.querySelector(".klai-meta")?.textContent).toBe(
      "Answer based on 1 source from the knowledge base.",
    );
  });
});

describe("plural label helpers", () => {
  it("treats zero as plural in both languages", () => {
    initLabels("nl");
    expect(sourceCountLabel(0)).toBe("0 bronnen");
    expect(activityCountLabel(0)).toBe("0 stappen");
    initLabels("en");
    expect(sourceCountLabel(0)).toBe("0 sources");
    expect(activityCountLabel(0)).toBe("0 steps");
  });

  it("uses the singular only for exactly one, in both languages", () => {
    initLabels("nl");
    expect(sourceCountLabel(1)).toBe("1 bron");
    expect(activityCountLabel(1)).toBe("1 stap");
    expect(answerBasedOnSourcesLabel(1)).toBe("Antwoord gebaseerd op 1 bron uit de kennisbank.");
    initLabels("en");
    expect(sourceCountLabel(1)).toBe("1 source");
    expect(activityCountLabel(1)).toBe("1 step");
    expect(answerBasedOnSourcesLabel(1)).toBe("Answer based on 1 source from the knowledge base.");
  });

  it("pluralizes counts greater than one", () => {
    initLabels("nl");
    expect(sourceCountLabel(3)).toBe("3 bronnen");
    initLabels("en");
    expect(sourceCountLabel(3)).toBe("3 sources");
  });
});
