import { For, Show } from "solid-js";
import DOMPurify from "dompurify";
import snarkdown from "snarkdown";
import { TypingIndicator } from "./TypingIndicator";
import { t } from "../i18n/labels";

const MALFORMED_CITATION_RE = /(^|[^\[])\b(\d+)\((https?:\/\/[^)\s]+)\)/g;

function normalizeCitationMarkdown(text: string): string {
  return text.replace(MALFORMED_CITATION_RE, (_match, prefix: string, label: string, url: string) => {
    return `${prefix}[${label}](${url})`;
  });
}

function decorateLinks(html: string): string {
  const template = document.createElement("template");
  template.innerHTML = html;

  template.content.querySelectorAll("a").forEach((anchor) => {
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noopener noreferrer");

    const label = anchor.textContent?.trim() ?? "";
    if (/^\d+$/.test(label)) {
      anchor.classList.add("klai-citation");
      anchor.textContent = `[${label}]`;
    }
  });

  return template.innerHTML;
}

function renderMarkdown(text: string): string {
  const markdown = normalizeCitationMarkdown(text);
  const sanitized = DOMPurify.sanitize(snarkdown(markdown));
  return decorateLinks(sanitized);
}
import type { Message } from "../api/chat-stream";

interface MessageListProps {
  messages: Message[];
  isStreaming: boolean;
  error: string | null;
}

export function MessageList(props: MessageListProps) {
  let listRef: HTMLDivElement | undefined;

  // Auto-scroll to bottom when messages change
  const scrollToBottom = () => {
    if (listRef) {
      listRef.scrollTop = listRef.scrollHeight;
    }
  };

  return (
    <div
      class="klai-messages"
      ref={listRef}
      role="log"
      aria-label={t().messagesLabel}
      aria-live="polite"
    >
      <For each={props.messages}>
        {(message) => {
          // Skip empty assistant messages that are still being streamed
          const isEmpty = message.role === "assistant" && message.content === "" && props.isStreaming;
          return (
            <Show when={!isEmpty}>
              <div
                class={`klai-message klai-message--${message.role}`}
                aria-label={`${message.role === "user" ? "You" : "Assistant"}: ${message.content}`}
              >
                {message.role === "user" ? (
                  message.content
                ) : (
                  <div class="klai-markdown" innerHTML={renderMarkdown(message.content)} />
                )}
              </div>
            </Show>
          );
        }}
      </For>

      <Show when={props.isStreaming}>
        <TypingIndicator />
      </Show>

      <Show when={props.error !== null}>
        <div class="klai-error" role="alert">
          {props.error}
        </div>
      </Show>

      {/* Invisible sentinel to auto-scroll */}
      <div ref={(el) => { void el; scrollToBottom(); }} />
    </div>
  );
}
