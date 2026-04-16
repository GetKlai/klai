import { createSignal, Show } from "solid-js";
import { ChatWindow } from "./ChatWindow";
import { chatState } from "../store/chat";

export function ChatBubble() {
  const [isOpen, setIsOpen] = createSignal(false);

  const toggle = () => setIsOpen((v) => !v);
  const close = () => setIsOpen(false);

  const title = () => chatState.config?.title ?? "Chat";

  return (
    <>
      <Show when={isOpen()}>
        <ChatWindow title={title()} onClose={close} />
      </Show>

      <button
        class="klai-bubble"
        aria-label={isOpen() ? "Sluit chat" : "Open chat"}
        aria-expanded={isOpen()}
        onClick={toggle}
      >
        <Show
          when={isOpen()}
          fallback={
            /* Chat icon */
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z" />
            </svg>
          }
        >
          /* Close / chevron-down icon */
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z" />
          </svg>
        </Show>
      </button>
    </>
  );
}
