import { For, Show } from "solid-js";
import DOMPurify from "dompurify";
import snarkdown from "snarkdown";
import { TypingIndicator } from "./TypingIndicator";
import { t } from "../i18n/labels";
import type { Message, MessageSource } from "../api/chat-stream";
import { normalizeSourceUrl } from "../api/chat-stream";

const MALFORMED_CITATION_RE = /(^|[^\[])\b(\d+)\((https?:\/\/[^)\s]+)\)/g;
const CITATION_MARKER_TEST_RE = /\(\d+(?:,\d+)*\)/;
const CITATION_MARKER_RE = /\((\d+(?:,\d+)*)\)/g;

function normalizeCitationMarkdown(text: string): string {
  return text.replace(MALFORMED_CITATION_RE, (_match, prefix: string, label: string, url: string) => {
    return `${prefix}[${label}](${url})`;
  });
}

function sourceMapFromSources(sources?: MessageSource[]): Map<string, MessageSource> {
  const sourceMap = new Map<string, MessageSource>();
  for (const source of sources ?? []) {
    const label = source.label.trim();
    const url = normalizeSourceUrl(source.url);
    if (!/^\d+$/.test(label) || !url || sourceMap.has(label)) {
      continue;
    }
    sourceMap.set(label, {
      label,
      title: source.title?.trim() || `Source ${label}`,
      url,
    });
  }
  return sourceMap;
}

function decorateLinks(template: HTMLTemplateElement): void {
  template.content.querySelectorAll("a").forEach((anchor) => {
    const href = normalizeSourceUrl(anchor.getAttribute("href"));
    if (!href) {
      anchor.replaceWith(document.createTextNode(anchor.textContent ?? ""));
      return;
    }

    anchor.setAttribute("href", href);
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noopener noreferrer");

    const label = anchor.textContent?.trim() ?? "";
    const citationLabel = label.match(/^\[?(\d+)\]?$/)?.[1];
    if (citationLabel) {
      anchor.classList.add("klai-citation");
      anchor.textContent = `(${citationLabel})`;
    }
  });
}

function linkCitationMarkers(template: HTMLTemplateElement, sources?: MessageSource[]): void {
  const sourcesByLabel = sourceMapFromSources(sources);
  if (sourcesByLabel.size === 0) {
    return;
  }

  const textNodes: Text[] = [];
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (parent?.closest("a, code, pre")) {
        return NodeFilter.FILTER_REJECT;
      }
      return CITATION_MARKER_TEST_RE.test(node.textContent ?? "")
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT;
    },
  });

  let node = walker.nextNode();
  while (node) {
    textNodes.push(node as Text);
    node = walker.nextNode();
  }

  for (const textNode of textNodes) {
    const text = textNode.textContent ?? "";
    CITATION_MARKER_RE.lastIndex = 0;
    let lastIndex = 0;
    let changed = false;
    const fragment = document.createDocumentFragment();

    for (const match of text.matchAll(CITATION_MARKER_RE)) {
      const labels = match[1].split(",");
      const matchedText = match[0];
      const start = match.index ?? 0;
      const matchedSources = labels.map((label) => sourcesByLabel.get(label));
      if (matchedSources.some((source) => !source)) {
        continue;
      }

      if (start > lastIndex) {
        fragment.append(document.createTextNode(text.slice(lastIndex, start)));
      }

      const group = document.createElement("span");
      group.className = "klai-citation-group";
      group.append(document.createTextNode("("));
      matchedSources.forEach((source, index) => {
        if (!source) {
          return;
        }
        if (index > 0) {
          group.append(document.createTextNode(","));
        }
        const anchor = document.createElement("a");
        anchor.className = "klai-citation";
        anchor.href = source.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.title = source.title;
        anchor.textContent = source.label;
        group.append(anchor);
      });
      group.append(document.createTextNode(")"));
      fragment.append(group);

      lastIndex = start + matchedText.length;
      changed = true;
    }

    if (!changed) {
      continue;
    }
    if (lastIndex < text.length) {
      fragment.append(document.createTextNode(text.slice(lastIndex)));
    }
    textNode.replaceWith(fragment);
  }
}

function renderMarkdown(text: string, sources?: MessageSource[]): string {
  const markdown = normalizeCitationMarkdown(text);
  const template = document.createElement("template");
  template.innerHTML = DOMPurify.sanitize(snarkdown(markdown));
  decorateLinks(template);
  linkCitationMarkers(template, sources);
  return template.innerHTML;
}

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
          // Skip ANY empty assistant message (streaming placeholder OR
          // an empty welcome_message seeded by the store). Renders the
          // pre-content small empty bubble the user reported as weird.
          const isEmpty =
            message.role === "assistant" && message.content.trim() === "";
          return (
            <Show when={!isEmpty}>
              <div
                class={`klai-message klai-message--${message.role}`}
                aria-label={`${message.role === "user" ? "You" : "Assistant"}: ${message.content}`}
              >
                {message.role === "user" ? (
                  message.content
                ) : (
                  <div class="klai-markdown" innerHTML={renderMarkdown(message.content, message.sources)} />
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
