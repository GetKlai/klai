import { createSignal, Show } from "solid-js";
import { ChatWindow } from "./ChatWindow";
import { chatState } from "../store/chat";
import { t } from "../i18n/labels";

export function ChatBubble() {
  const [isOpen, setIsOpen] = createSignal(false);

  const toggle = () => setIsOpen((v) => !v);
  const close = () => setIsOpen(false);

  const title = () => chatState.config?.title ?? "Chat";

  return (
    <>
      <Show when={isOpen()}>
        <ChatWindow
          title={title()}
          description={chatState.config?.description}
          onClose={close}
          conversationStarters={chatState.config?.conversation_starters}
          hideDisclaimer={chatState.config?.hide_disclaimer}
          welcomeMessage={chatState.config?.welcome_message}
          collectUserInfo={chatState.config?.collect_user_info}
        />
      </Show>

      <button
        class="klai-bubble"
        aria-label={isOpen() ? t().closeChat : t().openChat}
        aria-expanded={isOpen()}
        onClick={toggle}
      >
        <Show
          when={isOpen()}
          fallback={
            /* Line/open chat icon — same outline glyph as the header
               avatar, /bot share-page header, and the admin widgets
               list. Single icon across every Klai chat surface. */
            <svg
              viewBox="0 0 24 24"
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              stroke-width="1.75"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          }
        >
          {/* Chevron-down when open (collapses the chat). */}
          <svg
            viewBox="0 0 24 24"
            aria-hidden="true"
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </Show>
      </button>
    </>
  );
}
