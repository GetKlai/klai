import { createEffect, createSignal, onCleanup, Show } from "solid-js";
import { MessageList } from "./MessageList";
import {
  chatState,
  addAgentMessage,
  addAssistantNotice,
  addUserMessage,
  closeCurrentConversation,
  createConversationSessionId,
  createTurnId,
  startAssistantMessage,
  appendToLastMessage,
  setLastMessageSources,
  setLastMessageBroadMode,
  setBroadMode,
  appendLastMessageActivity,
  finishStreaming,
  setError,
  clearError,
  setHandoffActive,
  setHandoffConnecting,
  setVisitorIdentity,
  setRememberIdentity,
  clearStoredIdentity,
  startNewConversation,
  switchConversation,
} from "../store/chat";
import { collectPageContext, streamChat } from "../api/chat-stream";
import { fetchWidgetConfig } from "../api/widget-config";
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
  // White-label toggle for the accuracy footer only — it must never hide
  // the EU AI Act art. 50 notice in the hero.
  hideDisclaimer?: boolean;
  welcomeMessage?: string;
  bookingUrl?: string;
  collectUserInfo?: boolean;
  manageHandoffStream?: boolean;
}

// TWD-pattern widget chrome:
//   header  → primary-color bg, avatar + title + description, close
//   hero    → centered icon + welcome line + mandatory AI notice +
//             starter chips (only when the conversation hasn't started yet)
//   input   → pill textarea + small primary-color send button
//   footer  → AI accuracy disclaimer (white-label toggle hides it)
export function ChatWindow(props: ChatWindowProps) {
  const [inputValue, setInputValue] = createSignal("");
  const [visitorName, setVisitorName] = createSignal(chatState.visitorName);
  const [visitorEmail, setVisitorEmail] = createSignal(chatState.visitorEmail);
  const [showHistory, setShowHistory] = createSignal(false);
  const [conversationActionBusy, setConversationActionBusy] = createSignal(false);
  let abortController: AbortController | null = null;
  let handoffAbortController: AbortController | null = null;
  let handoffStreamToken: string | null = null;
  let textareaRef: HTMLTextAreaElement | undefined;
  const seenHandoffMessageIds = new Set<number>();

  // The art. 50 notice doubles as a screen-reader announcement on window
  // open. Screen readers only reliably announce a live region whose
  // content CHANGES after the region is already in the DOM — text shipped
  // in the same mount as the region itself is skipped. So the hero renders
  // the (empty) status region immediately and fills the text one beat
  // later; closing and reopening the window re-announces it.
  const [aiDisclosureText, setAiDisclosureText] = createSignal("");
  const disclosureTimer = window.setTimeout(() => {
    // Fill the notice with the tenant's own bot name. config.name is the
    // per-widget display name the admin API requires (non-empty); the
    // header title is only a caption and may be generic wording that
    // reads wrong mid-sentence. No/blank name → the prepared no-org
    // variant, so the sentence never renders a hole.
    const botName = chatState.config?.name?.trim();
    setAiDisclosureText(
      botName ? t().aiDisclosure.replace("{name}", botName) : t().aiDisclosureNoOrg,
    );
  }, 150);
  onCleanup(() => window.clearTimeout(disclosureTimer));

  const connectHandoffStream = () => {
    if (props.manageHandoffStream === false || !chatState.sessionToken) {
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
          handoffStreamToken = null;
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
    chatState.conversationStatus !== "closed" &&
    visitorInfoComplete() &&
    !chatState.isStreaming &&
    !chatState.handoffConnecting &&
    !conversationActionBusy();

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

  // One streamed bot turn. The consent flag and retrieval-query override
  // travel with the request; JSON.stringify drops them when unset, so
  // regular strict traffic is byte-identical to before broad mode existed.
  const streamBotTurn = async (opts: { retrievalQuery?: string } = {}) => {
    const turnId = createTurnId();
    startAssistantMessage(turnId);

    abortController = new AbortController();

    await streamChat({
      endpoint: chatState.config!.chat_endpoint,
      token: chatState.sessionToken,
      widgetId: chatState.widgetId,
      messages: withVisitorInfo(chatState.messages.slice(0, -1)),
      widgetTurnId: turnId,
      broadMode: chatState.broadMode || undefined,
      retrievalQuery: opts.retrievalQuery,
      pageContext: chatState.config?.page_context_enabled ? collectPageContext() : undefined,
      abortController,
      callbacks: {
        onToken: (token) => {
          appendToLastMessage(token);
        },
        onSources: (sources) => {
          setLastMessageSources(sources);
        },
        onActivity: (activity) => {
          appendLastMessageActivity(activity);
        },
        onBroadMode: (mode) => {
          setLastMessageBroadMode(mode);
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

  // ── Helpdesk broad mode: consent handling ────────────────────────────
  // Consent is only ever a complete, unpunctuated affirmation typed right
  // after the bot offered it, or a click on the offer button. An exact
  // word-list match (no fuzzy "does it contain ja") keeps "ja, maar ik
  // liever niet" a normal message the strict bot can answer.

  const BROAD_CONSENT_WORDS = new Set([
    "ja", "jawel", "jazeker", "ja zeker", "ja graag", "ja dat mag", "ja doe maar",
    "doe maar", "graag", "ok", "oke", "oké", "akkoord", "ga maar", "ga door",
    "yes", "yeah", "yep", "yes please", "please", "sure", "of course", "go ahead", "y",
  ]);

  const isBroadConsentText = (text: string): boolean => {
    const normalized = text
      .toLowerCase()
      .replace(/[.,!?;:'"`"“”‘’\u00b7()\[\]]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    return BROAD_CONSENT_WORDS.has(normalized);
  };

  // The last message the bot actually said something in — empty streaming
  // placeholders don't count and don't hide an offer one turn older.
  const lastBotAnswerIndex = (): number => {
    for (let i = chatState.messages.length - 1; i >= 0; i--) {
      const message = chatState.messages[i];
      if (message.role === "assistant" && message.content.trim()) return i;
    }
    return -1;
  };

  const questionBefore = (index: number): string => {
    for (let i = index - 1; i >= 0; i--) {
      const message = chatState.messages[i];
      if (message.role === "user" && message.content.trim()) return message.content;
    }
    return "";
  };

  const runBroadConsentTurn = async (question: string) => {
    setBroadMode(true);
    // Retrieval on this turn runs against the original question, not the
    // consent text: the knowledge gap must keep being recorded for what the
    // visitor actually asked.
    await streamBotTurn({ retrievalQuery: question || undefined });
  };

  const handleBroadConsentClick = async (offerIndex: number) => {
    if (!canSend()) return;
    clearError();
    addUserMessage(t().broadConsentMessage);
    if (textareaRef) {
      textareaRef.style.height = "auto";
    }
    await runBroadConsentTurn(questionBefore(offerIndex));
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
          visitorName: visitorName(),
        });
      } catch {
        setError(t().errorGeneric);
      }
      return;
    }

    // A bare "yes" answering the bot's broad-mode offer is consent, not a
    // question: run the same turn the offer button would.
    if (!chatState.broadMode && isBroadConsentText(content)) {
      const answerIndex = lastBotAnswerIndex();
      if (answerIndex >= 0 && chatState.messages[answerIndex].broadMode === "offer") {
        await runBroadConsentTurn(questionBefore(answerIndex));
        return;
      }
    }

    await streamBotTurn();
  };

  const handleStop = () => {
    if (abortController) {
      abortController.abort();
      abortController = null;
      finishStreaming();
    }
  };

  const startHandoff = async () => {
    if (chatState.conversationStatus === "closed" || chatState.handoffActive || chatState.handoffConnecting || chatState.isStreaming) {
      return;
    }
    clearError();
    setHandoffConnecting(true);
    addAssistantNotice(t().handoffConnecting);
    try {
      await startHubSpotHandoff({
        token: chatState.sessionToken,
        messages: chatState.messages,
        visitorName: visitorName(),
        visitorEmail: visitorEmail(),
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
    if (chatState.handoffActive && chatState.sessionToken && chatState.conversationStatus !== "closed") {
      connectHandoffStream();
      return;
    }
    if (handoffAbortController) {
      handoffAbortController.abort();
      handoffAbortController = null;
      handoffStreamToken = null;
    }
  });

  onCleanup(() => {
    if (handoffAbortController) {
      handoffAbortController.abort();
      handoffAbortController = null;
      handoffStreamToken = null;
    }
  });

  const openConversation = async (conversationId: string) => {
    if (conversationId === chatState.clientSessionId || chatState.isStreaming || conversationActionBusy()) {
      setShowHistory(false);
      return;
    }
    setConversationActionBusy(true);
    clearError();
    try {
      const config = await fetchWidgetConfig(chatState.widgetId, { sessionId: conversationId });
      switchConversation(config, conversationId);
      setShowHistory(false);
      setInputValue("");
    } catch {
      setError(t().errorGeneric);
    } finally {
      setConversationActionBusy(false);
    }
  };

  const startFreshConversation = async () => {
    if (chatState.isStreaming || conversationActionBusy()) return;
    setConversationActionBusy(true);
    clearError();
    try {
      const conversationId = createConversationSessionId();
      const config = await fetchWidgetConfig(chatState.widgetId, { sessionId: conversationId });
      startNewConversation(config, conversationId);
      setInputValue("");
      setShowHistory(false);
    } catch {
      setError(t().errorGeneric);
    } finally {
      setConversationActionBusy(false);
    }
  };

  const closeConversation = () => {
    if (chatState.isStreaming || conversationActionBusy()) return;
    closeCurrentConversation();
    setShowHistory(false);
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
          <div class="klai-header-actions">
            <button
              class={showHistory() ? "klai-icon-btn klai-icon-btn--active" : "klai-icon-btn"}
              type="button"
              aria-label={t().conversationHistory}
              title={t().conversationHistory}
              onClick={() => setShowHistory((value) => !value)}
            >
              <svg class={showHistory() ? "klai-menu-icon klai-menu-icon--open" : "klai-menu-icon"} viewBox="0 0 24 24" aria-hidden="true">
                <path class="klai-menu-icon-line klai-menu-icon-line--top" d="M4 7h16" />
                <path class="klai-menu-icon-line klai-menu-icon-line--middle" d="M4 12h16" />
                <path class="klai-menu-icon-line klai-menu-icon-line--bottom" d="M4 17h16" />
              </svg>
            </button>
            <button
              class="klai-icon-btn"
              type="button"
              aria-label={t().newConversation}
              title={t().newConversation}
              disabled={chatState.isStreaming || conversationActionBusy()}
              onClick={() => void startFreshConversation()}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 5v14" />
                <path d="M5 12h14" />
              </svg>
            </button>
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

      <Show when={showHistory()}>
        <div class="klai-conversation-panel">
          <div class="klai-conversation-panel-head">
            <span>{t().conversationHistory}</span>
            <button
              type="button"
              class="klai-conversation-close-btn"
              disabled={chatState.conversationStatus === "closed" || chatState.isStreaming}
              onClick={closeConversation}
            >
              {t().closeConversation}
            </button>
          </div>
          <div class="klai-conversation-list">
            <Show
              when={chatState.conversations.length > 0}
              fallback={<p class="klai-conversation-empty">{t().noPreviousConversations}</p>}
            >
              {chatState.conversations.map((conversation) => (
                <button
                  type="button"
                  class={
                    conversation.id === chatState.clientSessionId
                      ? "klai-conversation-item klai-conversation-item--active"
                      : "klai-conversation-item"
                  }
                  disabled={conversationActionBusy() || chatState.isStreaming}
                  onClick={() => void openConversation(conversation.id)}
                >
                  <span class="klai-conversation-title">{conversation.title}</span>
                  <span class="klai-conversation-meta">
                    {conversation.status === "closed"
                      ? t().conversationClosed
                      : conversation.status === "handoff_active"
                        ? t().conversationHandoff
                        : t().conversationActive}
                  </span>
                </button>
              ))}
            </Show>
          </div>
          <button
            type="button"
            class="klai-new-conversation-btn"
            disabled={chatState.isStreaming || conversationActionBusy()}
            onClick={() => void startFreshConversation()}
          >
            {t().newConversation}
          </button>
        </div>
      </Show>

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
          {/* EU AI Act art. 50 notice: visitors must know they are talking
              to an AI system, perceptibly, at first interaction. Deliberately
              NOT gated on hide_disclaimer — that flag is a white-label toggle
              for the accuracy footer ("AI-antwoorden kunnen fouten bevatten…")
              and a legal disclosure cannot be switched off per widget. It also
              sits NEXT TO the configurable welcome_message rather than inside
              it, so a customer can rewrite the greeting without ever dropping
              the notice. role="status" + the deferred fill above announce it
              to screen readers when the window opens. */}
          <p class="klai-hero-ai-disclosure" role="status" aria-live="polite">
            {aiDisclosureText()}
          </p>
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
          onBroadConsent={(offerIndex) => void handleBroadConsentClick(offerIndex)}
        />
      </Show>

      {/* Broad-mode indicator: once the visitor consented (or ever received
        a labelled broad answer in this conversation), the mode stays
        visible and switchable. Turning it off returns the bot to strict
        help-articles-only answers immediately; turning it back on is a
        fresh explicit click. */}
      <Show
        when={
          hasUserTurn() &&
          (chatState.broadMode || chatState.messages.some((m) => m.broadMode === "answer"))
        }
      >
        <div class="klai-broad-status">
          <span class="klai-broad-status-label">
            {chatState.broadMode ? t().broadModeOnLabel : t().broadModePausedLabel}
          </span>
          <button
            type="button"
            class="klai-broad-toggle-btn"
            disabled={chatState.isStreaming || chatState.conversationStatus === "closed"}
            onClick={() => setBroadMode(!chatState.broadMode)}
          >
            {chatState.broadMode ? t().broadModeOffButton : t().broadModeOnButton}
          </button>
        </div>
      </Show>

      <Show when={chatState.handoffActive}>
        <div class="klai-handoff-status">
          {chatState.agentName
            ? t().handoffConnectedWith.replace("{name}", chatState.agentName)
            : t().handoffConnected}
        </div>
      </Show>

      <Show when={chatState.conversationStatus === "closed"}>
        <div class="klai-handoff-status">{t().conversationClosed}</div>
      </Show>

      <Show when={hasUserTurn() && chatState.config?.handoff?.hubspot?.enabled && !chatState.handoffActive && chatState.conversationStatus !== "closed"}>
        <div class="klai-handoff-bar">
          <Show when={!props.collectUserInfo}>
            <input
              class="klai-handoff-name-input"
              type="text"
              autocomplete="name"
              placeholder={t().handoffNamePlaceholder}
              value={visitorName()}
              onInput={(e) => {
                const name = e.currentTarget.value;
                setVisitorName(name);
                setVisitorIdentity({ name });
              }}
            />
          </Show>
          <button
            type="button"
            class="klai-handoff-btn"
            disabled={chatState.handoffConnecting || chatState.isStreaming || visitorName().trim().length < 2}
            onClick={() => void startHandoff()}
          >
            {t().handoffButton}
          </button>
        </div>
      </Show>

      {/* INTERIM appointment redirect until the chat booking API
          integration lands: the SUPPORT prompt offers a personal
          appointment on escalation, the button here executes the
          redirect to the support partner's booking module. booking_url
          is server-validated to absolute http(s) before delivery
          (partner.py _widget_booking_url), so it is safe as an href.
          Unset → no element rendered, current behaviour unchanged. */}
      <Show when={props.bookingUrl?.trim()}>
        <div class="klai-booking-bar">
          <a
            class="klai-booking-btn"
            href={props.bookingUrl!.trim()}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t().bookingButton}
          </a>
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
                onInput={(e) => {
                  const name = e.currentTarget.value;
                  setVisitorName(name);
                  setVisitorIdentity({ name });
                }}
              />
              <input
                class="klai-user-info-input"
                type="email"
                autocomplete="email"
                placeholder={t().userInfoEmail}
                value={visitorEmail()}
                onInput={(e) => {
                  const email = e.currentTarget.value;
                  setVisitorEmail(email);
                  setVisitorIdentity({ email });
                }}
              />
            </div>
            <label class="klai-remember-me">
              <input
                type="checkbox"
                checked={chatState.rememberIdentity}
                onChange={(e) => setRememberIdentity(e.currentTarget.checked)}
              />
              <span>{t().rememberMe}</span>
            </label>
            <Show when={chatState.rememberIdentity && (visitorName() || visitorEmail())}>
              <button
                type="button"
                class="klai-clear-identity"
                onClick={() => {
                  setVisitorName("");
                  setVisitorEmail("");
                  clearStoredIdentity();
                }}
              >
                {t().clearStoredIdentity}
              </button>
            </Show>
          </div>
        </Show>
        <textarea
          ref={textareaRef}
          class="klai-textarea"
          placeholder={t().placeholder}
          value={inputValue()}
          onInput={handleTextareaInput}
          onKeyDown={handleKeyDown}
          disabled={chatState.isStreaming || chatState.handoffConnecting || chatState.conversationStatus === "closed" || conversationActionBusy()}
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
