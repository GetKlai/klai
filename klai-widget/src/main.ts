/// <reference types="vite/client" />
import { render } from "solid-js/web";
import { ChatWindow } from "./components/ChatWindow";
import { WidgetFacade } from "./components/WidgetFacade";
import { getInitialConversationSessionId, initStore, setChatOpen } from "./store/chat";
import { fetchWidgetConfig, KlaiWidgetError } from "./api/widget-config";
import type { WidgetConfig } from "./api/widget-config";
import { initLabels } from "./i18n/labels";
import widgetCss from "./styles/widget.css?inline";

// Find the script tag that loaded this widget
function findScriptTag(): HTMLScriptElement | null {
  // currentScript is available during synchronous script execution
  if (document.currentScript instanceof HTMLScriptElement) {
    return document.currentScript;
  }
  // Fallback: find last script tag with data-widget-id
  const scripts = document.querySelectorAll<HTMLScriptElement>(
    "script[data-widget-id]"
  );
  if (scripts.length > 0) {
    return scripts[scripts.length - 1];
  }
  return null;
}

function logFetchError(error: unknown): void {
  if (error instanceof KlaiWidgetError) {
    console.error(error.code);
  } else {
    console.error("KLAI_WIDGET_NETWORK_ERROR");
  }
}

async function loadConfigAndInitStore(
  widgetId: string,
  locale: string | undefined,
  clientSessionId: string,
): Promise<WidgetConfig> {
  const config = await fetchWidgetConfig(widgetId, { sessionId: clientSessionId });

  // Init i18n labels after config so the widget copy can hint the locale.
  initLabels(locale, [
    config.title,
    config.description ?? "",
    config.welcome_message,
    ...(config.conversation_starters ?? []),
  ]);

  // Initialize the store with config and widget ID
  initStore(widgetId, config, clientSessionId);

  return config;
}

// data-primary-color preview: strict hex (#rgb, #rrggbb, #rrggbbaa) only.
// The value comes from a third-party page and must never reach the
// stylesheet as raw text; anything else is silently dropped so the CSS
// defaults stand.
function parsePreviewPrimaryColor(value: string | null): string | null {
  if (value && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value)) {
    return value;
  }
  return null;
}

// css_variables from config as custom properties overrides
function cssVariableOverrides(config: WidgetConfig): string {
  return Object.entries(config.css_variables)
    .map(([key, value]) => `${key}: ${value};`)
    .join(" ");
}

async function bootstrap(): Promise<void> {
  const scriptTag = findScriptTag();

  if (!scriptTag) {
    console.error("KLAI_WIDGET: Could not find script tag with data-widget-id");
    return;
  }

  const widgetId = scriptTag.getAttribute("data-widget-id");
  if (!widgetId) {
    console.error("KLAI_WIDGET: data-widget-id attribute is missing or empty");
    return;
  }

  const mode = scriptTag.getAttribute("data-mode") ?? "bubble";
  const locale = scriptTag.getAttribute("data-locale") ?? undefined;
  const containerSelector = scriptTag.getAttribute("data-container");
  const clientSessionId = getInitialConversationSessionId(widgetId);

  if (mode === "inline" && containerSelector) {
    // Inline mode has no facade: the chat window sits on the page
    // deliberately, so the config is fetched immediately as before.
    let config;
    try {
      config = await loadConfigAndInitStore(widgetId, locale, clientSessionId);
    } catch (error) {
      logFetchError(error);
      return;
    }

    setChatOpen(true);
    // Inline mode: mount ChatWindow directly into a page element, no shadow DOM
    const target = document.querySelector(containerSelector);
    if (!target) {
      console.error(`KLAI_WIDGET: Container "${containerSelector}" not found`);
      return;
    }

    // Inject scoped styles into the page (no shadow DOM in inline mode)
    const styleEl = document.createElement("style");
    const inlineOverrides = cssVariableOverrides(config);
    styleEl.textContent = inlineOverrides
      ? `${widgetCss}\n.klai-inline-root { ${inlineOverrides} }`
      : widgetCss;
    document.head.appendChild(styleEl);

    target.classList.add("klai-inline-root");

    render(
      () => ChatWindow({
        title: config.title,
        description: config.description,
        onClose: () => {},
        inline: true,
        conversationStarters: config.conversation_starters,
        hideDisclaimer: config.hide_disclaimer,
        welcomeMessage: config.welcome_message,
        bookingUrl: config.booking_url,
        collectUserInfo: config.collect_user_info,
      }),
      target as HTMLElement,
    );
    return;
  }

  // Bubble mode (default): Shadow DOM floating widget. Only the facade
  // renders at page load — no network request until the first click —
  // so visitors who never open the chat cost no config call and no
  // session token.
  const container = document.createElement("div");
  container.setAttribute("id", "klai-widget-root");
  document.body.appendChild(container);

  const shadowRoot = container.attachShadow({ mode: "open" });

  const styleEl = document.createElement("style");
  // The optional data-primary-color preview lands in the stylesheet
  // before the facade renders, so the bubble starts in the customer's
  // brand colour instead of the Klai default. The config-driven :host
  // override in launch() replaces it as soon as the config arrives.
  const previewColor = parsePreviewPrimaryColor(
    scriptTag.getAttribute("data-primary-color"),
  );
  styleEl.textContent = previewColor
    ? `${widgetCss}\n:host { --klai-primary-color: ${previewColor}; }`
    : widgetCss;
  shadowRoot.appendChild(styleEl);

  const mountPoint = document.createElement("div");
  shadowRoot.appendChild(mountPoint);

  // The facade's own copy (aria-label, error hint) needs a locale
  // already; the config-driven hint re-initialises the labels at
  // launch time, exactly as before.
  initLabels(locale);

  // Runs on the first click and retried after a failed attempt; never
  // again once it resolved. On success the facade swaps to the real
  // ChatBubble with the chat window already open.
  const launch = async (): Promise<void> => {
    const config = await loadConfigAndInitStore(widgetId, locale, clientSessionId);
    const cssVariables = cssVariableOverrides(config);
    if (cssVariables) {
      styleEl.textContent = `${widgetCss}\n:host { ${cssVariables} }`;
    }
  };

  render(() => WidgetFacade({ launch }), mountPoint);
}

// Bootstrap asynchronously — never blocks host page
void bootstrap();
