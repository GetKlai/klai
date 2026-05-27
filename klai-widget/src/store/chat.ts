import { createStore } from "solid-js/store";
import type { WidgetConfig } from "../api/widget-config";
import type { Message, MessageSource } from "../api/chat-stream";
import { normalizeMessageSources } from "../api/chat-stream";

export interface ChatState {
  messages: Message[];
  // Session token stored in memory only — never in localStorage/sessionStorage/cookies
  sessionToken: string;
  widgetId: string;
  clientSessionId: string;
  isStreaming: boolean;
  config: WidgetConfig | null;
  error: string | null;
  handoffActive: boolean;
  handoffConnecting: boolean;
  lastHandoffEventId: number;
  unreadCount: number;
  isOpen: boolean;
  agentName: string | null;
  visitorName: string;
  visitorEmail: string;
}

const initialState: ChatState = {
  messages: [],
  sessionToken: "",
  widgetId: "",
  clientSessionId: "",
  isStreaming: false,
  config: null,
  error: null,
  handoffActive: false,
  handoffConnecting: false,
  lastHandoffEventId: 0,
  unreadCount: 0,
  isOpen: false,
  agentName: null,
  visitorName: "",
  visitorEmail: "",
};

export const [chatState, setChatState] = createStore<ChatState>(initialState);

interface PersistedChatState {
  version: 1;
  clientSessionId: string;
  messages: Message[];
  handoffActive: boolean;
  lastHandoffEventId: number;
  unreadCount: number;
  agentName: string | null;
  visitorName: string;
  visitorEmail: string;
}

function storageKey(widgetId: string): string {
  return `klai-widget:${widgetId}:chat:v1`;
}

function loadPersistedState(widgetId: string): PersistedChatState | null {
  try {
    const raw = window.localStorage.getItem(storageKey(widgetId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedChatState>;
    if (parsed.version !== 1 || !Array.isArray(parsed.messages)) return null;
    return {
      version: 1,
      clientSessionId: typeof parsed.clientSessionId === "string" ? parsed.clientSessionId : "",
      messages: parsed.messages.filter(
        (message): message is Message =>
          typeof message?.content === "string" &&
          (message.role === "user" || message.role === "assistant" || message.role === "agent"),
      ),
      handoffActive: parsed.handoffActive === true,
      lastHandoffEventId: Number(parsed.lastHandoffEventId || 0),
      unreadCount: Number(parsed.unreadCount || 0),
      agentName: typeof parsed.agentName === "string" && parsed.agentName.trim() ? parsed.agentName : null,
      visitorName: typeof parsed.visitorName === "string" ? parsed.visitorName.slice(0, 120) : "",
      visitorEmail: typeof parsed.visitorEmail === "string" ? parsed.visitorEmail.slice(0, 254) : "",
    };
  } catch {
    return null;
  }
}

function persistState(): void {
  if (!chatState.widgetId || !chatState.clientSessionId) return;
  try {
    const payload: PersistedChatState = {
      version: 1,
      clientSessionId: chatState.clientSessionId,
      messages: chatState.messages.slice(-80),
      handoffActive: chatState.handoffActive,
      lastHandoffEventId: chatState.lastHandoffEventId,
      unreadCount: chatState.unreadCount,
      agentName: chatState.agentName,
      visitorName: chatState.visitorName,
      visitorEmail: chatState.visitorEmail,
    };
    window.localStorage.setItem(storageKey(chatState.widgetId), JSON.stringify(payload));
  } catch {
    // Persistence is best-effort; the widget must keep working in private mode.
  }
}

function schedulePersist(): void {
  queueMicrotask(persistState);
}

export function initStore(widgetId: string, config: WidgetConfig, clientSessionId: string): void {
  const persisted = loadPersistedState(widgetId);
  const restoredMessages = persisted?.messages.length ? persisted.messages : null;
  setChatState({
    widgetId,
    config,
    clientSessionId,
    // Token stored in memory only
    sessionToken: config.session_token,
    messages: restoredMessages ?? [
      {
        role: "assistant",
        content: config.welcome_message,
      },
    ],
    isStreaming: false,
    error: null,
    handoffActive: persisted?.handoffActive === true,
    handoffConnecting: false,
    lastHandoffEventId: persisted?.lastHandoffEventId ?? 0,
    unreadCount: persisted?.unreadCount ?? 0,
    agentName: persisted?.agentName ?? null,
    visitorName: persisted?.visitorName ?? "",
    visitorEmail: persisted?.visitorEmail ?? "",
  });
  schedulePersist();
}

export function setVisitorIdentity(identity: { name?: string; email?: string }): void {
  if (identity.name !== undefined) {
    setChatState("visitorName", identity.name.slice(0, 120));
  }
  if (identity.email !== undefined) {
    setChatState("visitorEmail", identity.email.slice(0, 254));
  }
  schedulePersist();
}

export function addUserMessage(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "user", content }]);
  schedulePersist();
}

export function startAssistantMessage(): void {
  setChatState("messages", (msgs) => [...msgs, { role: "assistant", content: "" }]);
  setChatState("isStreaming", true);
  schedulePersist();
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
  schedulePersist();
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
  schedulePersist();
}

export function finishStreaming(): void {
  setChatState("isStreaming", false);
  schedulePersist();
}

export function setError(message: string): void {
  setChatState("error", message);
  setChatState("isStreaming", false);
  setChatState("handoffConnecting", false);
  schedulePersist();
}

export function clearError(): void {
  setChatState("error", null);
}

export function updateSessionToken(token: string): void {
  // Update token in memory — never persist to storage
  setChatState("sessionToken", token);
}

export function addAgentMessage(content: string, options: { id?: number; agentName?: string } = {}): void {
  const id = options.id && options.id > 0 ? options.id : undefined;
  if (id && id <= chatState.lastHandoffEventId) {
    return;
  }
  const agentName = options.agentName?.trim() || chatState.agentName || undefined;
  setChatState("messages", (msgs) => [...msgs, { role: "agent", content, id, agentName }]);
  if (id) {
    setChatState("lastHandoffEventId", Math.max(chatState.lastHandoffEventId, id));
  }
  if (agentName) {
    setChatState("agentName", agentName);
  }
  if (!chatState.isOpen) {
    setChatState("unreadCount", (count) => Math.min(count + 1, 99));
  }
  schedulePersist();
}

export function addAssistantNotice(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "assistant", content }]);
  schedulePersist();
}

export function setHandoffConnecting(value: boolean): void {
  setChatState("handoffConnecting", value);
  schedulePersist();
}

export function setHandoffActive(value: boolean): void {
  setChatState("handoffActive", value);
  setChatState("handoffConnecting", false);
  setChatState("isStreaming", false);
  schedulePersist();
}

export function setChatOpen(value: boolean): void {
  setChatState("isOpen", value);
  if (value) {
    setChatState("unreadCount", 0);
  }
  schedulePersist();
}
