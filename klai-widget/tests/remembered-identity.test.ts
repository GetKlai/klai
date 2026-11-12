import { afterEach, describe, expect, it } from "vitest";

import type { WidgetConfig } from "../src/api/widget-config";
import { chatState, createConversationSessionId, initStore } from "../src/store/chat";

const WIDGET_ID = "wgt_test";
const DAY_MS = 24 * 60 * 60 * 1000;

const config: WidgetConfig = {
  title: "Help",
  welcome_message: "Hi",
  css_variables: {},
  chat_endpoint: "https://api.example.test",
  session_token: "test-token",
  session_expires_at: new Date().toISOString(),
};

afterEach(() => window.localStorage.clear());

describe("remembered visitor details", () => {
  it("stay until the visitor clears them, however long ago they were saved", () => {
    window.localStorage.setItem(
      `klai-widget:${WIDGET_ID}:chat:v1`,
      JSON.stringify({
        version: 3,
        activeConversationId: "",
        identity: { name: "Sanne Voorbeeld", email: "sanne@example.com", savedAt: Date.now() - 400 * DAY_MS },
        conversations: [],
      }),
    );

    initStore(WIDGET_ID, config, createConversationSessionId());

    expect(chatState.visitorName).toBe("Sanne Voorbeeld");
    expect(chatState.visitorEmail).toBe("sanne@example.com");
    expect(chatState.rememberIdentity).toBe(true);
  });
});
