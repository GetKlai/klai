import { createStore } from "solid-js/store";
import type { WidgetConfig } from "../api/widget-config";
import type { Message, MessageSource } from "../api/chat-stream";
import { normalizeMessageSources } from "../api/chat-stream";

export interface ChatState {
  messages: Message[];
  // Session token stored in memory only — never in localStorage/sessionStorage/cookies
  sessionToken: string;
  widgetId: string;
  isStreaming: boolean;
  config: WidgetConfig | null;
  error: string | null;
  handoffActive: boolean;
  handoffConnecting: boolean;
}

const initialState: ChatState = {
  messages: [],
  sessionToken: "",
  widgetId: "",
  isStreaming: false,
  config: null,
  error: null,
  handoffActive: false,
  handoffConnecting: false,
};

export const [chatState, setChatState] = createStore<ChatState>(initialState);

export function initStore(widgetId: string, config: WidgetConfig): void {
  setChatState({
    widgetId,
    config,
    // Token stored in memory only
    sessionToken: config.session_token,
    messages: [
      {
        role: "assistant",
        content: config.welcome_message,
      },
    ],
    isStreaming: false,
    error: null,
    handoffActive: false,
    handoffConnecting: false,
  });
}

export function addUserMessage(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "user", content }]);
}

export function startAssistantMessage(): void {
  setChatState("messages", (msgs) => [...msgs, { role: "assistant", content: "" }]);
  setChatState("isStreaming", true);
}

export function appendToLastMessage(token: string): void {
  setChatState("messages", (msgs) => {
    const updated = [...msgs];
    const last = updated[updated.length - 1];
    if (last && last.role === "assistant") {
      updated[updated.length - 1] = { ...last, content: last.content + token };
    }
    return updated;
  });
}

export function setLastMessageSources(sources: MessageSource[]): void {
  const normalizedSources = normalizeMessageSources(sources);
  if (normalizedSources.length === 0) {
    return;
  }
  setChatState("messages", (msgs) => {
    const updated = [...msgs];
    const last = updated[updated.length - 1];
    if (last && last.role === "assistant") {
      updated[updated.length - 1] = { ...last, sources: normalizedSources };
    }
    return updated;
  });
}

export function finishStreaming(): void {
  setChatState("isStreaming", false);
}

export function setError(message: string): void {
  setChatState("error", message);
  setChatState("isStreaming", false);
  setChatState("handoffConnecting", false);
}

export function clearError(): void {
  setChatState("error", null);
}

export function updateSessionToken(token: string): void {
  // Update token in memory — never persist to storage
  setChatState("sessionToken", token);
}

export function addAgentMessage(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "agent", content }]);
}

export function addAssistantNotice(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "assistant", content }]);
}

export function setHandoffConnecting(value: boolean): void {
  setChatState("handoffConnecting", value);
}

export function setHandoffActive(value: boolean): void {
  setChatState("handoffActive", value);
  setChatState("handoffConnecting", false);
  setChatState("isStreaming", false);
}
