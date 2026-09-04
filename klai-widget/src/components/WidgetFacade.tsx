import { createSignal, Show } from "solid-js";
import { ChatBubble } from "./ChatBubble";
import { t } from "../i18n/labels";

export interface WidgetFacadeProps {
  // Fetches the widget config, initialises the store and applies the
  // theme. Resolves once the real widget is ready to swap in; rejects
  // when the fetch failed (the caller logs the error code).
  launch: () => Promise<void>;
}

// Facade bubble: a network-free stand-in for ChatBubble, drawn only
// from the CSS defaults and the script-tag attributes. Most visitors
// never open a chat widget, so the config request — and the session
// token it carries — waits for the first click.
export function WidgetFacade(props: WidgetFacadeProps) {
  const [ready, setReady] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [failed, setFailed] = createSignal(false);

  const handleClick = () => {
    if (ready() || loading()) {
      return;
    }
    setLoading(true);
    setFailed(false);
    props.launch().then(
      () => {
        setLoading(false);
        setReady(true);
      },
      () => {
        setLoading(false);
        setFailed(true);
      },
    );
  };

  return (
    <Show
      when={ready()}
      fallback={
        <>
          <Show when={failed()}>
            <div class="klai-facade-error" role="alert">
              {t().errorGeneric}
            </div>
          </Show>
          <button
            class="klai-bubble"
            aria-label={t().openChat}
            aria-expanded={false}
            aria-busy={loading()}
            onClick={handleClick}
          >
            <Show
              when={loading()}
              fallback={
                /* Same outline chat glyph as the loaded bubble, so the
                   facade is visually identical until the config lands. */
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
              <span class="klai-bubble-spinner" aria-hidden="true" />
            </Show>
          </button>
        </>
      }
    >
      <ChatBubble initiallyOpen />
    </Show>
  );
}
