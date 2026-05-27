import { createEffect, createSignal, onCleanup, Show } from "solid-js";
import { MessageList } from "./MessageList";
import {
  chatState,
  addAgentMessage,
  addAssistantNotice,
  addUserMessage,
  startAssistantMessage,
  appendToLastMessage,
  setLastMessageSources,
  finishStreaming,
  setError,
  clearError,
  setHandoffActive,
  setHandoffConnecting,
} from "../store/chat";
import { collectPageContext, streamChat } from "../api/chat-stream";
import {
  sendHubSpotHandoffMessage,
  startHubSpotHandoff,
  streamHubSpotHandoffEvents,
} from "../api/handoff";
import { t } from "../i18n/labels";
import type { Message } from "../api/chat-stream";

interface ChatWindowProps {
  title: string;
  description?: string;
  onClose: () => void;
  inline?: boolean;
  conversationStarters?: string[];
  hideDisclaimer?: boolean;
  welcomeMessage?: string;
  collectUserInfo?: boolean;
  manageHandoffStream?: boolean;
}

// TWD-pattern widget chrome:
//   header  → primary-color bg, avatar + title + description, close
//   hero    → centered icon + welcome line + starter chips (only when
//             the conversation hasn't started yet)
//   input   → pill textarea + small primary-color send button
//   footer  → AI disclaimer (white-label toggle hides it)
export function ChatWindow(props: ChatWindowProps) {
  const [inputValue, setInputValue] = createSignal("");
  const [visitorName, setVisitorName] = createSignal("");
  const [visitorEmail, setVisitorEmail] = createSignal("");
  let abortController: AbortController | null = null;
  let handoffAbortController: AbortController | null = null;
  let textareaRef: HTMLTextAreaElement | undefined;
  const seenHandoffMessageIds = new Set<number>();

  const connectHandoffStream = () => {
    if (props.manageHandoffStream === false || handoffAbortController) {
      return;
    }
    handoffAbortController = new AbortController();
    void streamHubSpotHandoffEvents({
      token: chatState.sessionToken,
      lastEventId: chatState.lastHandoffEventId,
      abortController: handoffAbortController,
      callbacks: {
        onAgentMessage: (content, id, agentName) => {
          if (id && seenHandoffMessageIds.has(id)) {
            return;
          }
          if (id) {
            seenHandoffMessageIds.add(id);
          }
          addAgentMessage(content, { id, agentName });
        },
        onError: () => {
          setError(t().errorGeneric);
          handoffAbortController = null;
        },
      },
    });
  };

  const handleStarterClick = (text: string) => {
    if (chatState.isStreaming) return;
    setInputValue(text);
    if (canSend()) {
      void handleSend(text);
    }
  };

  const visitorInfoComplete = () =>
    !props.collectUserInfo ||
    (visitorName().trim().length > 1 && visitorEmail().trim().includes("@"));

  const canSend = () =>
    visitorInfoComplete() && !chatState.isStreaming && !chatState.handoffConnecting;

  const withVisitorInfo = (messages: Message[]): Message[] => {
    if (!props.collectUserInfo) return messages;
    const name = visitorName().trim();
    const email = visitorEmail().trim();
    if (!name || !email) return messages;
    let added = false;
    return messages.map((message) => {
      if (added || message.role !== "user") return message;
      added = true;
      return {
        ...message,
        content: `Visitor details:\nName: ${name}\nEmail: ${email}\n\nMessage:\n${message.content}`,
      };
    });
  };

  const handleSend = async (override?: string) => {
    const content = (override ?? inputValue()).trim();
    if (!content || !canSend()) return;

    clearError();
    addUserMessage(content);
    setInputValue("");

    if (textareaRef) {
      textareaRef.style.height = "auto";
    }

    if (chatState.handoffActive) {
      try {
        await sendHubSpotHandoffMessage({
          token: chatState.sessionToken,
          content,
        });
      } catch {
        setError(t().errorGeneric);
      }
      return;
    }

    startAssistantMessage();

    abortController = new AbortController();

    await streamChat({
      endpoint: chatState.config!.chat_endpoint,
      token: chatState.sessionToken,
      widgetId: chatState.widgetId,
      messages: withVisitorInfo(chatState.messages.slice(0, -1)),
      pageContext: chatState.config?.page_context_enabled ? collectPageContext() : undefined,
      abortController,
      callbacks: {
        onToken: (token) => {
          appendToLastMessage(token);
        },
        onSources: (sources) => {
          setLastMessageSources(sources);
        },
        onDone: () => {
          finishStreaming();
          abortController = null;
        },
        onError: (error) => {
          finishStreaming();
          abortController = null;
          setError(
            error.message.includes("Origin")
              ? t().errorSessionExpired
              : t().errorGeneric
          );
        },
      },
    });
  };

  const handleStop = () => {
    if (abortController) {
      abortController.abort();
      abortController = null;
      finishStreaming();
    }
  };

  const startHandoff = async () => {
    if (chatState.handoffActive || chatState.handoffConnecting || chatState.isStreaming) {
      return;
    }
    clearError();
    setHandoffConnecting(true);
    addAssistantNotice(t().handoffConnecting);
    try {
      await startHubSpotHandoff({
        token: chatState.sessionToken,
        messages: chatState.messages,
      });
      setHandoffActive(true);
      addAssistantNotice(t().handoffConnected);
      connectHandoffStream();
    } catch {
      setError(t().errorGeneric);
    }
  };

  createEffect(() => {
    if (props.manageHandoffStream === false) {
      return;
    }
    if (chatState.handoffActive && chatState.sessionToken) {
      connectHandoffStream();
    }
  });

  onCleanup(() => {
    if (handoffAbortController) {
      handoffAbortController.abort();
      handoffAbortController = null;
    }
  });

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const handleTextareaInput = (e: InputEvent) => {
    const target = e.target as HTMLTextAreaElement;
    setInputValue(target.value);
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
  };

  // A conversation has started once the user has sent any message. The
  // welcome message that ships from the store does NOT count — until
  // the first user turn we show the hero, not the welcome bubble.
  const hasUserTurn = () =>
    chatState.messages.some((m) => m.role === "user");

  return (
    <div
      class={props.inline ? "klai-window klai-window--inline" : "klai-window"}
      role={props.inline ? "region" : "dialog"}
      aria-label={props.title}
      aria-modal={props.inline ? undefined : "false"}
    >
      {!props.inline && (
        <div class="klai-header">
          <div class="klai-header-id">
            <span class="klai-header-avatar" aria-hidden="true">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.75"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </span>
            <div class="klai-header-text">
              <span class="klai-header-title">{props.title}</span>
              <Show when={props.description}>
                <span class="klai-header-description">{props.description}</span>
              </Show>
            </div>
          </div>
          <button
            class="klai-close-btn"
            aria-label={t().closeChat}
            onClick={props.onClose}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      )}

      {/* Empty-state hero — TWD pattern. Shows the bot identity + a
          row of starter chips. The instant the user sends a message
          we drop the hero and switch to the regular message list. */}
      <Show when={!hasUserTurn()}>
        <div class="klai-hero">
          <div class="klai-hero-icon" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <p class="klai-hero-title">
            {props.welcomeMessage?.trim() || props.title}
          </p>
          <Show when={props.description}>
            <p class="klai-hero-subtitle">{props.description}</p>
          </Show>
          <Show when={(props.conversationStarters?.length ?? 0) > 0}>
            <div class="klai-starters">
              {props.conversationStarters!.map((s) => (
                <button
                  type="button"
                  class="klai-starter"
                  onClick={() => handleStarterClick(s)}
                  disabled={chatState.isStreaming}
                >
                  {s}
                </button>
              ))}
            </div>
          </Show>
        </div>
      </Show>

      <Show when={hasUserTurn()}>
        <MessageList
          messages={chatState.messages}
          isStreaming={chatState.isStreaming}
          error={chatState.error}
        />
      </Show>

      <Show when={chatState.handoffActive}>
        <div class="klai-handoff-status">
          {chatState.agentName
            ? t().handoffConnectedWith.replace("{name}", chatState.agentName)
            : t().handoffConnected}
        </div>
      </Show>

      <Show when={hasUserTurn() && chatState.config?.handoff?.hubspot?.enabled && !chatState.handoffActive}>
        <div class="klai-handoff-bar">
          <button
            type="button"
            class="klai-handoff-btn"
            disabled={chatState.handoffConnecting || chatState.isStreaming}
            onClick={() => void startHandoff()}
          >
            {t().handoffButton}
          </button>
        </div>
      </Show>

      <div class="klai-input-area">
        <Show when={props.collectUserInfo}>
          <div class="klai-user-info" aria-label={t().userInfoHelp}>
            <p>{t().userInfoHelp}</p>
            <div class="klai-user-info-fields">
              <input
                class="klai-user-info-input"
                type="text"
                autocomplete="name"
                placeholder={t().userInfoName}
                value={visitorName()}
                onInput={(e) => setVisitorName(e.currentTarget.value)}
              />
              <input
                class="klai-user-info-input"
                type="email"
                autocomplete="email"
                placeholder={t().userInfoEmail}
                value={visitorEmail()}
                onInput={(e) => setVisitorEmail(e.currentTarget.value)}
              />
            </div>
          </div>
        </Show>
        <textarea
          ref={textareaRef}
          class="klai-textarea"
          placeholder={t().placeholder}
          value={inputValue()}
          onInput={handleTextareaInput}
          onKeyDown={handleKeyDown}
          disabled={chatState.isStreaming || chatState.handoffConnecting}
          rows={1}
          aria-label={t().inputLabel}
        />
        <Show
          when={chatState.isStreaming}
          fallback={
            <button
              class="klai-send-btn"
              aria-label={t().sendMessage}
              disabled={inputValue().trim() === "" || !canSend()}
              onClick={() => void handleSend()}
            >
              <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            </button>
          }
        >
          <button
            class="klai-stop-btn"
            aria-label={t().stopGenerating}
            onClick={handleStop}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        </Show>
      </div>

      <Show when={!props.hideDisclaimer}>
        <p class="klai-disclaimer">{t().disclaimer}</p>
      </Show>
    </div>
  );
}
