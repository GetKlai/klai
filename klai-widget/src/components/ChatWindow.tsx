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
  onClose: () => void;
  inline?: boolean;
  conversationStarters?: string[];
  hideDisclaimer?: boolean;
  welcomeMessage?: string;
}

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
      messages: chatState.messages.slice(0, -1), // Exclude the empty assistant placeholder
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
    // Auto-resize textarea
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
  };

  return (
    <div class={props.inline ? "klai-window klai-window--inline" : "klai-window"} role={props.inline ? "region" : "dialog"} aria-label={props.title} aria-modal={props.inline ? undefined : "false"}>
      {!props.inline && (
        <div class="klai-header">
          <div class="klai-header-id">
            <span class="klai-header-avatar" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </span>
            <div class="klai-header-text">
              <span class="klai-header-title">{props.title}</span>
              <span class="klai-header-status">
                <span class="klai-header-status-dot" aria-hidden="true" />
                Online
              </span>
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

      <MessageList
        messages={chatState.messages}
        isStreaming={chatState.isStreaming}
        error={chatState.error}
      />

      {/* Empty-state chips: shown only when no messages yet (excluding
          the welcome placeholder), and only if the admin configured at
          least one conversation starter. Click submits as the first user
          message. */}
      <Show when={chatState.messages.length <= 1 && (props.conversationStarters?.length ?? 0) > 0}>
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
