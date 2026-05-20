import { createSignal, Show } from "solid-js";
import { MessageList } from "./MessageList";
import {
  chatState,
  addUserMessage,
  startAssistantMessage,
  appendToLastMessage,
  setLastMessageSources,
  finishStreaming,
  setError,
  clearError,
} from "../store/chat";
import { streamChat } from "../api/chat-stream";
import { t } from "../i18n/labels";

interface ChatWindowProps {
  title: string;
  description?: string;
  onClose: () => void;
  inline?: boolean;
  conversationStarters?: string[];
  hideDisclaimer?: boolean;
  welcomeMessage?: string;
}

// TWD-pattern widget chrome:
//   header  → primary-color bg, avatar + title + description, close
//   hero    → centered icon + welcome line + starter chips (only when
//             the conversation hasn't started yet)
//   input   → pill textarea + small primary-color send button
//   footer  → AI disclaimer (white-label toggle hides it)
export function ChatWindow(props: ChatWindowProps) {
  const [inputValue, setInputValue] = createSignal("");
  let abortController: AbortController | null = null;
  let textareaRef: HTMLTextAreaElement | undefined;

  const handleStarterClick = (text: string) => {
    if (chatState.isStreaming) return;
    setInputValue(text);
    void handleSend(text);
  };

  const handleSend = async (override?: string) => {
    const content = (override ?? inputValue()).trim();
    if (!content || chatState.isStreaming) return;

    clearError();
    addUserMessage(content);
    setInputValue("");

    if (textareaRef) {
      textareaRef.style.height = "auto";
    }

    startAssistantMessage();

    abortController = new AbortController();

    await streamChat({
      endpoint: chatState.config!.chat_endpoint,
      token: chatState.sessionToken,
      widgetId: chatState.widgetId,
      messages: chatState.messages.slice(0, -1),
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
                <rect x="3" y="4" width="18" height="13" rx="2" />
                <path d="M8 21h8" />
                <path d="M12 17v4" />
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

      <div class="klai-input-area">
        <textarea
          ref={textareaRef}
          class="klai-textarea"
          placeholder={t().placeholder}
          value={inputValue()}
          onInput={handleTextareaInput}
          onKeyDown={handleKeyDown}
          disabled={chatState.isStreaming}
          rows={1}
          aria-label={t().inputLabel}
        />
        <Show
          when={chatState.isStreaming}
          fallback={
            <button
              class="klai-send-btn"
              aria-label={t().sendMessage}
              disabled={inputValue().trim() === ""}
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
