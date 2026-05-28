import { createStore } from "solid-js/store";
import type { WidgetConfig } from "../api/widget-config";
import type { AgentActivity, Message, MessageSource } from "../api/chat-stream";
import { normalizeAgentActivity, normalizeMessageSources } from "../api/chat-stream";

export type ConversationStatus = "active" | "handoff_active" | "closed";

export interface ConversationListItem {
  id: string;
  title: string;
  status: ConversationStatus;
  updatedAt: number;
  unreadCount: number;
  agentName: string | null;
}

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
  // Opt-in flag controlling whether visitorName/visitorEmail are
  // persisted to localStorage across sessions. False by default — the
  // current session keeps both in memory and ships them to the handoff
  // API, but a fresh browser tab on a shared computer starts blank.
  rememberIdentity: boolean;
  conversationStatus: ConversationStatus;
  conversations: ConversationListItem[];
}

// Stored identity is wiped after this many milliseconds. The visitor's
// next visit starts blank and re-prompts. Chosen at 30 days to match
// the industry convention used by Intercom/Drift for the same opt-in.
const IDENTITY_TTL_MS = 30 * 24 * 60 * 60 * 1000;

interface PersistedConversation {
  id: string;
  messages: Message[];
  handoffActive: boolean;
  lastHandoffEventId: number;
  unreadCount: number;
  agentName: string | null;
  status: ConversationStatus;
  createdAt: number;
  updatedAt: number;
}

interface PersistedWidgetStateV1 {
  version: 1;
  clientSessionId: string;
  messages: Message[];
  handoffActive: boolean;
  lastHandoffEventId: number;
  unreadCount: number;
  agentName: string | null;
  visitorName?: string;
  visitorEmail?: string;
}

interface PersistedWidgetStateV2 {
  version: 2;
  activeConversationId: string;
  visitorName: string;
  visitorEmail: string;
  conversations: PersistedConversation[];
}

interface PersistedWidgetStateV3 {
  version: 3;
  activeConversationId: string;
  // Visitor identity is only persisted when the visitor explicitly
  // opted in via the "Remember me" checkbox. The TTL is enforced on
  // read so an expired entry surfaces as blank without an extra wipe
  // pass.
  identity: {
    name: string;
    email: string;
    savedAt: number;
  } | null;
  conversations: PersistedConversation[];
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
  rememberIdentity: false,
  conversationStatus: "active",
  conversations: [],
};

export const [chatState, setChatState] = createStore<ChatState>(initialState);

let persistedConversations: Record<string, PersistedConversation> = {};

function storageKey(widgetId: string): string {
  return `klai-widget:${widgetId}:chat:v1`;
}

function legacySessionKey(widgetId: string): string {
  return `klai-widget:${widgetId}:session:v1`;
}

function defaultMessages(config: WidgetConfig): Message[] {
  return [
    {
      role: "assistant",
      content: config.welcome_message,
    },
  ];
}

function isValidSessionId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{16,80}$/.test(value);
}

export function createConversationSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 18)}`;
}

export function getInitialConversationSessionId(widgetId: string): string {
  try {
    const raw = window.localStorage.getItem(storageKey(widgetId));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<
        PersistedWidgetStateV1 | PersistedWidgetStateV2 | PersistedWidgetStateV3
      >;
      if (parsed.version === 3 && isValidSessionId(parsed.activeConversationId)) {
        return parsed.activeConversationId;
      }
      if (parsed.version === 2 && isValidSessionId(parsed.activeConversationId)) {
        return parsed.activeConversationId;
      }
      if (parsed.version === 1 && isValidSessionId(parsed.clientSessionId)) {
        return parsed.clientSessionId;
      }
    }

    const legacy = window.localStorage.getItem(legacySessionKey(widgetId));
    if (isValidSessionId(legacy)) {
      return legacy;
    }

    const next = createConversationSessionId();
    window.localStorage.setItem(legacySessionKey(widgetId), next);
    return next;
  } catch {
    return createConversationSessionId();
  }
}

function normalizeMessages(value: unknown): Message[] {
  return Array.isArray(value)
    ? value.filter(
        (message): message is Message =>
          typeof message?.content === "string" &&
          (message.role === "user" || message.role === "assistant" || message.role === "agent"),
      )
    : [];
}

function normalizeConversation(value: Partial<PersistedConversation>): PersistedConversation | null {
  if (!isValidSessionId(value.id)) return null;
  const messages = normalizeMessages(value.messages).slice(-80);
  const now = Date.now();
  const status: ConversationStatus =
    value.status === "closed" || value.status === "handoff_active" || value.status === "active"
      ? value.status
      : value.handoffActive
        ? "handoff_active"
        : "active";
  return {
    id: value.id,
    messages,
    handoffActive: value.handoffActive === true || status === "handoff_active",
    lastHandoffEventId: Number(value.lastHandoffEventId || 0),
    unreadCount: Number(value.unreadCount || 0),
    agentName: typeof value.agentName === "string" && value.agentName.trim() ? value.agentName : null,
    status,
    createdAt: Number(value.createdAt || now),
    updatedAt: Number(value.updatedAt || now),
  };
}

function normalizeIdentity(
  raw: unknown,
  now: number,
): PersistedWidgetStateV3["identity"] {
  if (!raw || typeof raw !== "object") return null;
  const candidate = raw as {
    name?: unknown;
    email?: unknown;
    savedAt?: unknown;
  };
  const savedAt = Number(candidate.savedAt ?? 0);
  if (!savedAt || !Number.isFinite(savedAt)) return null;
  if (savedAt > now) return null; // future timestamp = corrupted entry
  if (now - savedAt > IDENTITY_TTL_MS) return null; // expired → wipe on next persist
  const name = typeof candidate.name === "string" ? candidate.name.slice(0, 120) : "";
  const email = typeof candidate.email === "string" ? candidate.email.slice(0, 254) : "";
  if (!name && !email) return null;
  return { name, email, savedAt };
}

function loadPersistedState(widgetId: string, fallbackConversationId: string): PersistedWidgetStateV3 | null {
  try {
    const raw = window.localStorage.getItem(storageKey(widgetId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<
      PersistedWidgetStateV1 | PersistedWidgetStateV2 | PersistedWidgetStateV3
    >;
    const now = Date.now();

    if (parsed.version === 3) {
      const conversations = Array.isArray(parsed.conversations)
        ? parsed.conversations
            .map((conversation) => normalizeConversation(conversation))
            .filter((conversation): conversation is PersistedConversation => conversation !== null)
            .slice(0, 20)
        : [];
      return {
        version: 3,
        activeConversationId: isValidSessionId(parsed.activeConversationId)
          ? parsed.activeConversationId
          : fallbackConversationId,
        identity: normalizeIdentity(parsed.identity, now),
        conversations,
      };
    }

    if (parsed.version === 2) {
      // v2 persisted identity unconditionally. Treat the upgrade path as
      // a privacy reset: drop the identity, keep the conversation list,
      // and let the visitor re-opt-in via the new checkbox.
      const conversations = Array.isArray(parsed.conversations)
        ? parsed.conversations
            .map((conversation) => normalizeConversation(conversation))
            .filter((conversation): conversation is PersistedConversation => conversation !== null)
            .slice(0, 20)
        : [];
      return {
        version: 3,
        activeConversationId: isValidSessionId(parsed.activeConversationId)
          ? parsed.activeConversationId
          : fallbackConversationId,
        identity: null,
        conversations,
      };
    }

    if (parsed.version === 1) {
      const id = isValidSessionId(parsed.clientSessionId) ? parsed.clientSessionId : fallbackConversationId;
      const migrated = normalizeConversation({
        id,
        messages: parsed.messages,
        handoffActive: parsed.handoffActive,
        lastHandoffEventId: parsed.lastHandoffEventId,
        unreadCount: parsed.unreadCount,
        agentName: parsed.agentName,
        status: parsed.handoffActive ? "handoff_active" : "active",
      });
      return {
        version: 3,
        activeConversationId: id,
        identity: null,
        conversations: migrated ? [migrated] : [],
      };
    }
  } catch {
    return null;
  }
  return null;
}

function titleFromMessages(messages: Message[]): string {
  const firstUser = messages.find((message) => message.role === "user" && message.content.trim());
  const title = firstUser?.content.replace(/\s+/g, " ").trim() || "Nieuw gesprek";
  return title.length > 46 ? `${title.slice(0, 43)}...` : title;
}

function snapshotCurrentConversation(status = chatState.conversationStatus): PersistedConversation {
  const now = Date.now();
  const existing = persistedConversations[chatState.clientSessionId];
  const handoffActive = status === "closed" ? false : status === "handoff_active" || chatState.handoffActive;
  return {
    id: chatState.clientSessionId,
    messages: chatState.messages.slice(-80),
    handoffActive,
    lastHandoffEventId: chatState.lastHandoffEventId,
    unreadCount: chatState.unreadCount,
    agentName: chatState.agentName,
    status: handoffActive ? "handoff_active" : status,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };
}

function conversationList(): ConversationListItem[] {
  return Object.values(persistedConversations)
    .map((conversation) => ({
      id: conversation.id,
      title: titleFromMessages(conversation.messages),
      status: conversation.status,
      updatedAt: conversation.updatedAt,
      unreadCount: conversation.unreadCount,
      agentName: conversation.agentName,
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, 20);
}

function persistState(status = chatState.conversationStatus): void {
  if (!chatState.widgetId || !chatState.clientSessionId) return;
  try {
    persistedConversations[chatState.clientSessionId] = snapshotCurrentConversation(status);
    const conversations = Object.values(persistedConversations)
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, 20);
    persistedConversations = Object.fromEntries(conversations.map((conversation) => [conversation.id, conversation]));
    // Visitor identity is only persisted when the visitor opted in.
    // Without the opt-in we ship the current name/email for the live
    // session (in-memory state) but localStorage stays clean — a
    // shared computer never leaks a previous visitor's email.
    const identity: PersistedWidgetStateV3["identity"] =
      chatState.rememberIdentity && (chatState.visitorName || chatState.visitorEmail)
        ? {
            name: chatState.visitorName,
            email: chatState.visitorEmail,
            savedAt: Date.now(),
          }
        : null;
    const payload: PersistedWidgetStateV3 = {
      version: 3,
      activeConversationId: chatState.clientSessionId,
      identity,
      conversations,
    };
    window.localStorage.setItem(storageKey(chatState.widgetId), JSON.stringify(payload));
    setChatState("conversations", conversationList());
  } catch {
    // Persistence is best-effort; the widget must keep working in private mode.
  }
}

function schedulePersist(status?: ConversationStatus): void {
  queueMicrotask(() => persistState(status));
}

export function initStore(widgetId: string, config: WidgetConfig, clientSessionId: string): void {
  const persisted = loadPersistedState(widgetId, clientSessionId);
  persistedConversations = Object.fromEntries((persisted?.conversations ?? []).map((conversation) => [conversation.id, conversation]));
  const activeId = persisted?.activeConversationId && persistedConversations[persisted.activeConversationId]
    ? persisted.activeConversationId
    : clientSessionId;
  const activeConversation = persistedConversations[activeId];
  if (!activeConversation) {
    persistedConversations[activeId] = {
      id: activeId,
      messages: defaultMessages(config),
      handoffActive: false,
      lastHandoffEventId: 0,
      unreadCount: 0,
      agentName: null,
      status: "active",
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
  }
  const conversation = persistedConversations[activeId];
  setChatState({
    widgetId,
    config,
    clientSessionId: activeId,
    // Token stored in memory only
    sessionToken: config.session_token,
    messages: conversation.messages.length ? conversation.messages : defaultMessages(config),
    isStreaming: false,
    error: null,
    handoffActive: conversation.handoffActive,
    handoffConnecting: false,
    lastHandoffEventId: conversation.lastHandoffEventId,
    unreadCount: conversation.unreadCount,
    agentName: conversation.agentName,
    visitorName: persisted?.identity?.name ?? "",
    visitorEmail: persisted?.identity?.email ?? "",
    // If a persisted identity survived the load (= visitor previously
    // opted in AND the entry is still within TTL), the checkbox stays
    // checked on next visit. Otherwise default off.
    rememberIdentity: persisted?.identity != null,
    conversationStatus: conversation.status,
    conversations: conversationList(),
  });
  schedulePersist();
}

export function switchConversation(config: WidgetConfig, conversationId: string): void {
  if (!isValidSessionId(conversationId)) return;
  if (chatState.clientSessionId) {
    persistedConversations[chatState.clientSessionId] = snapshotCurrentConversation();
  }
  const existing = persistedConversations[conversationId] ?? {
    id: conversationId,
    messages: defaultMessages(config),
    handoffActive: false,
    lastHandoffEventId: 0,
    unreadCount: 0,
    agentName: null,
    status: "active" as ConversationStatus,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  persistedConversations[conversationId] = { ...existing, unreadCount: 0 };
  setChatState({
    config,
    clientSessionId: conversationId,
    sessionToken: config.session_token,
    messages: existing.messages.length ? existing.messages : defaultMessages(config),
    isStreaming: false,
    error: null,
    handoffActive: existing.handoffActive,
    handoffConnecting: false,
    lastHandoffEventId: existing.lastHandoffEventId,
    unreadCount: 0,
    agentName: existing.agentName,
    conversationStatus: existing.status,
    conversations: conversationList(),
  });
  schedulePersist();
}

export function startNewConversation(config: WidgetConfig, conversationId: string): void {
  if (chatState.clientSessionId) {
    persistedConversations[chatState.clientSessionId] = snapshotCurrentConversation("closed");
  }
  const now = Date.now();
  persistedConversations[conversationId] = {
    id: conversationId,
    messages: defaultMessages(config),
    handoffActive: false,
    lastHandoffEventId: 0,
    unreadCount: 0,
    agentName: null,
    status: "active",
    createdAt: now,
    updatedAt: now,
  };
  setChatState({
    config,
    clientSessionId: conversationId,
    sessionToken: config.session_token,
    messages: defaultMessages(config),
    isStreaming: false,
    error: null,
    handoffActive: false,
    handoffConnecting: false,
    lastHandoffEventId: 0,
    unreadCount: 0,
    agentName: null,
    conversationStatus: "active",
    conversations: conversationList(),
  });
  schedulePersist();
}

export function closeCurrentConversation(): void {
  setChatState("conversationStatus", "closed");
  setChatState("handoffActive", false);
  setChatState("handoffConnecting", false);
  setChatState("isStreaming", false);
  setChatState("unreadCount", 0);
  schedulePersist("closed");
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

export function setRememberIdentity(value: boolean): void {
  setChatState("rememberIdentity", value);
  schedulePersist();
}

export function clearStoredIdentity(): void {
  // Reset both the live session and the persisted entry. The opt-in
  // flag goes back to off so a fresh "Remember me" tick is needed to
  // re-persist.
  setChatState("visitorName", "");
  setChatState("visitorEmail", "");
  setChatState("rememberIdentity", false);
  schedulePersist();
}

export function addUserMessage(content: string): void {
  setChatState("messages", (msgs) => [...msgs, { role: "user", content }]);
  setChatState("conversationStatus", chatState.handoffActive ? "handoff_active" : "active");
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

export function appendLastMessageActivity(activity: AgentActivity[]): void {
  const normalizedActivity = normalizeAgentActivity(activity);
  if (normalizedActivity.length === 0) {
    return;
  }
  setChatState("messages", (msgs) => {
    const updated = [...msgs];
    const last = updated[updated.length - 1];
    if (last && last.role === "assistant") {
      const existing = last.activity ?? [];
      const existingSteps = new Set(existing.map((item) => item.step));
      const appended = normalizedActivity.filter((item) => !existingSteps.has(item.step));
      updated[updated.length - 1] = { ...last, activity: [...existing, ...appended] };
    }
    return updated;
  });
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
  setChatState("conversationStatus", "handoff_active");
  schedulePersist("handoff_active");
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
  setChatState("conversationStatus", value ? "handoff_active" : "active");
  setChatState("handoffConnecting", false);
  setChatState("isStreaming", false);
  schedulePersist(value ? "handoff_active" : "active");
}

export function setChatOpen(value: boolean): void {
  setChatState("isOpen", value);
  if (value) {
    setChatState("unreadCount", 0);
  }
  schedulePersist();
}
