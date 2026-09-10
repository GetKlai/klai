import { afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@solidjs/testing-library";

const widgetConfig = (title: string, token: string) => ({
  title,
  welcome_message: `Welcome ${title}`,
  css_variables: {},
  chat_endpoint: "/partner/v1/chat/completions",
  session_token: token,
  session_id: 'previewConversation012345',
  tenant_css_variables: { '--klai-message-gap': '20px', '--klai-background-color': '#ffffff' },
  session_expires_at: "2099-01-01T00:00:00Z",
  page_context_enabled: true,
});

afterEach(() => {
  document.body.innerHTML = "";
  document.head.innerHTML = "";
  localStorage.clear();
  vi.resetModules();
});

describe("admin preview", () => {
  it("uses isolated memory state and updates config without losing the conversation", async () => {
    const persisted = JSON.stringify({
      version: 3,
      activeConversationId: "persistedConversation123",
      identity: null,
      conversations: [{
        id: "persistedConversation123",
        messages: [{ role: "user", content: "Real visitor message" }],
        status: "active",
        createdAt: 1,
        updatedAt: 1,
      }],
    });
    localStorage.setItem("klai-widget:preview-widget:chat:v1", persisted);
    const script = document.createElement("script");
    script.dataset.mode = "preview";
    script.dataset.widgetId = "preview-widget";
    document.head.append(script);

    const { mountPreview } = await import("../src/main");
    const { addUserMessage, chatState } = await import("../src/store/chat");
    const parentWindow = window.parent;
    Object.defineProperty(window, "parent", { configurable: true, value: {} });
    const host = document.createElement("div");
    document.body.append(host);
    const fetchConfig = vi.fn(async () => widgetConfig("Restarted", "restart-token"));
    const preview = mountPreview(host, {
      widgetId: "preview-widget",
      locale: "nl",
      config: widgetConfig("Draft", "draft-token"),
      fetchConfig,
    });

    expect(chatState.messages.some((message) => message.content === "Real visitor message")).toBe(false);
    expect(chatState.clientSessionId).toBe('previewConversation012345');
    expect(host.style.getPropertyValue('--klai-message-gap')).toBe('20px');
    expect(host.style.getPropertyValue('--klai-background-color')).toBe('#ffffff');
    addUserMessage("Unsaved preview question");
    preview.updateConfig({ ...widgetConfig("Updated", "updated-token"), primary_color: "#270697" });

    expect(chatState.messages.some((message) => message.content === "Unsaved preview question")).toBe(true);
    expect(chatState.config?.title).toBe("Updated");
    expect(chatState.config?.page_context_enabled).toBe(false);
    expect(chatState.sessionToken).toBe("updated-token");
    expect(host.style.getPropertyValue("--klai-primary-color")).toBe("#270697");
    await Promise.resolve();
    expect(localStorage.getItem("klai-widget:preview-widget:chat:v1")).toBe(persisted);

    host.shadowRoot!.querySelector<HTMLButtonElement>('.klai-new-conversation-header-btn')!.click();
    await waitFor(() => expect(fetchConfig).toHaveBeenCalledWith(chatState.clientSessionId));

    preview.dispose();
    Object.defineProperty(window, "parent", { configurable: true, value: parentWindow });
  });
});
