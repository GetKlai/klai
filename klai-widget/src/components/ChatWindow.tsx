import { createSignal, Show } from "solid-js";
import { MessageList } from "./MessageList";
import {
  chatState,
  addUserMessage,
  startAssistantMessage,
  appendToLastMessage,
  finishStreaming,
  setError,
  clearError,
} from "../store/chat";
import { streamChat } from "../api/chat-stream";

interface ChatWindowProps {
  title: string;
  onClose: () => void;
}

export function ChatWindow(props: ChatWindowProps) {
  const [inputValue, setInputValue] = createSignal("");
  let abortController: AbortController | null = null;
  let textareaRef: HTMLTextAreaElement | undefined;

  const handleSend = async () => {
    const content = inputValue().trim();
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
        onDone: () => {
          finishStreaming();
          abortController = null;
        },
        onError: (error) => {
          finishStreaming();
          abortController = null;
          setError(
            error.message.includes("Origin")
              ? "Sessie verlopen. Herlaad de pagina."
              : "Er ging iets mis. Probeer het opnieuw."
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
    <div class="klai-window" role="dialog" aria-label={props.title} aria-modal="false">
      <div class="klai-header">
        <span class="klai-header-title">{props.title}</span>
        <button
          class="klai-close-btn"
          aria-label="Sluit chat"
          onClick={props.onClose}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <MessageList
        messages={chatState.messages}
        isStreaming={chatState.isStreaming}
        error={chatState.error}
      />

      <div class="klai-input-area">
        <textarea
          ref={textareaRef}
          class="klai-textarea"
          placeholder="Stel een vraag..."
          value={inputValue()}
          onInput={handleTextareaInput}
          onKeyDown={handleKeyDown}
          disabled={chatState.isStreaming}
          rows={1}
          aria-label="Berichtinvoer"
        />
        <Show
          when={chatState.isStreaming}
          fallback={
            <button
              class="klai-send-btn"
              aria-label="Stuur bericht"
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
            aria-label="Stop genereren"
            onClick={handleStop}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          </button>
        </Show>
      </div>
    </div>
  );
}
