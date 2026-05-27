import { createEffect, createSignal, onCleanup, Show } from "solid-js";
import { ChatWindow } from "./ChatWindow";
import { streamHubSpotHandoffEvents } from "../api/handoff";
import { addAgentMessage, chatState, setChatOpen, setError } from "../store/chat";
import { t } from "../i18n/labels";

export function ChatBubble() {
  const [isOpen, setIsOpen] = createSignal(false);
  let handoffAbortController: AbortController | null = null;
  let handoffStreamToken: string | null = null;
  const seenHandoffMessageIds = new Set<number>();

  const open = () => {
    setIsOpen(true);
    setChatOpen(true);
  };
  const close = () => {
    setIsOpen(false);
    setChatOpen(false);
  };
  const toggle = () => (isOpen() ? close() : open());

  const title = () => chatState.config?.title ?? "Chat";

  const connectHandoffStream = () => {
    if (!chatState.sessionToken) {
      return;
    }
    if (handoffAbortController && handoffStreamToken === chatState.sessionToken) {
      return;
    }
    if (handoffAbortController) {
      handoffAbortController.abort();
    }
    handoffStreamToken = chatState.sessionToken;
    handoffAbortController = new AbortController();
    void streamHubSpotHandoffEvents({
      token: chatState.sessionToken,
      lastEventId: chatState.lastHandoffEventId,
      abortController: handoffAbortController,
      callbacks: {
        onAgentMessage: (content, id, agentName) => {
          if (id && seenHandoffMessageIds.has(id)) return;
          if (id) seenHandoffMessageIds.add(id);
          addAgentMessage(content, { id, agentName });
        },
        onError: () => {
          setError(t().errorGeneric);
          handoffAbortController = null;
          handoffStreamToken = null;
        },
      },
    });
  };

  createEffect(() => {
    if (!chatState.handoffActive || chatState.conversationStatus === "closed") {
      if (handoffAbortController) {
        handoffAbortController.abort();
        handoffAbortController = null;
        handoffStreamToken = null;
      }
      return;
    }
    connectHandoffStream();
  });

  onCleanup(() => {
    if (handoffAbortController) {
      handoffAbortController.abort();
      handoffAbortController = null;
      handoffStreamToken = null;
    }
  });

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
          manageHandoffStream={false}
        />
      </Show>

      <button
        class="klai-bubble"
        aria-label={isOpen() ? t().closeChat : t().openChat}
        aria-expanded={isOpen()}
        onClick={toggle}
      >
        <Show when={!isOpen() && chatState.unreadCount > 0}>
          <span class="klai-bubble-badge" aria-label={`${chatState.unreadCount} unread messages`}>
            {chatState.unreadCount > 9 ? "9+" : chatState.unreadCount}
          </span>
        </Show>
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
