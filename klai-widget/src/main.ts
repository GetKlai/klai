/// <reference types="vite/client" />
import { render } from "solid-js/web";
import { ChatBubble } from "./components/ChatBubble";
import { ChatWindow } from "./components/ChatWindow";
import { WidgetFacade } from "./components/WidgetFacade";
import {
  getInitialConversationSessionId,
  initStore,
  setChatOpen,
  updateWidgetConfig,
} from "./store/chat";
import {
  fetchWidgetConfig,
  KlaiWidgetError,
  setPreviewWidgetConfigProvider,
} from "./api/widget-config";
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
  reuseCachedSession: boolean,
): Promise<WidgetConfig> {
  const config = await fetchWidgetConfig(widgetId, {
    sessionId: clientSessionId,
    reuseCachedSession,
  });

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

function previewPrimaryTextColor(primaryColor: string): string {
  const hex = primaryColor.slice(1);
  const rgb = (hex.length === 3 ? [...hex].map((digit) => digit + digit).join("") : hex)
    .slice(0, 6)
    .match(/.{2}/g)!
    .map((channel) => Number.parseInt(channel, 16) / 255)
    .map((channel) =>
      channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
    );
  const luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  return luminance > 0.179 ? "#191918" : "#ffffff";
}

// css_variables from config as custom properties overrides
function cssVariableOverrides(config: WidgetConfig): string {
  return Object.entries(config.css_variables)
    .map(([key, value]) => `${key}: ${value};`)
    .join(" ");
}

type PreviewConfig = WidgetConfig & {
  primary_color?: string;
  session_id?: string;
  tenant_css_variables?: Record<string, string>;
};

export interface PreviewOptions {
  widgetId: string;
  locale?: string;
  config: PreviewConfig;
  fetchConfig: (sessionId: string) => Promise<PreviewConfig>;
}

const previewGeometry = `
:host { display: block; position: relative; width: 100%; height: 100%; }
.klai-window:not(.klai-window--inline) { position: absolute; inset: 0; width: 100%; height: 100%; max-width: none; max-height: none; }
.klai-bubble { position: absolute; }
.klai-bubble[aria-expanded="true"] { display: none; }
`;

function previewConfig(config: PreviewConfig): PreviewConfig {
  return { ...config, page_context_enabled: false };
}

function previewAppearance(config: PreviewConfig): Map<string, string> {
  const values = new Map<string, string>(Object.entries(config.tenant_css_variables ?? {}));
  const theme = config.theme === "dark"
    ? [
        ["--klai-text-color", "#fffef2"], ["--klai-text-muted", "#fffef299"],
        ["--klai-background-color", "#191918"], ["--klai-card-color", "#27251f"],
        ["--klai-border-color", "#3a3831"],
      ]
    : [];
  for (const [key, value] of theme) values.set(key, value);
  const directPrimary = parsePreviewPrimaryColor(config.primary_color ?? null);
  const rawPrimary = parsePreviewPrimaryColor(config.css_variables["--klai-primary-color"] ?? null);
  const effectivePrimary = rawPrimary ?? directPrimary ?? parsePreviewPrimaryColor(values.get("--klai-primary-color") ?? null);
  if (directPrimary) values.set("--klai-primary-color", directPrimary);
  if (effectivePrimary) values.set("--klai-primary-text-color", previewPrimaryTextColor(effectivePrimary));
  for (const [key, value] of Object.entries(config.css_variables)) {
    if (key.startsWith("--klai-")) values.set(key, value);
  }
  return values;
}

export function mountPreview(host: HTMLElement, options: PreviewOptions) {
  if (host.ownerDocument !== document || window.parent === window) {
    throw new Error("KLAI_WIDGET_PREVIEW_IFRAME_REQUIRED");
  }
  const shadowRoot = host.shadowRoot ?? host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = `${widgetCss}\n${previewGeometry}`;
  const mountPoint = document.createElement("div");
  shadowRoot.replaceChildren(style, mountPoint);
  let applied = new Set<string>();
  const applyConfig = (input: PreviewConfig) => {
    const config = previewConfig(input);
    for (const key of applied) host.style.removeProperty(key);
    const values = previewAppearance(config);
    for (const [key, value] of values) host.style.setProperty(key, value);
    applied = new Set(values.keys());
    initLabels(options.locale, [config.title, config.welcome_message]);
    return config;
  };
  const sessionId = options.config.session_id ?? crypto.randomUUID().replace(/-/g, "");
  const initialConfig = applyConfig(options.config);
  setPreviewWidgetConfigProvider(async (_widgetId, fetchOptions) =>
    previewConfig(await options.fetchConfig(fetchOptions.sessionId ?? sessionId))
  );
  initStore(options.widgetId, initialConfig, sessionId, false);
  const disposeRoot = render(() => ChatBubble({ initiallyOpen: true }), mountPoint);

  return {
    updateConfig(input: PreviewConfig) {
      updateWidgetConfig(applyConfig(input));
    },
    dispose() {
      disposeRoot();
      setPreviewWidgetConfigProvider(undefined);
      for (const key of applied) host.style.removeProperty(key);
      shadowRoot.replaceChildren();
    },
  };
}

// The snippet often lands in <head> (help.voys.nl, most CMSs), so the bundle
// runs before <body> is parsed. Wait for it; the script tag itself must be
// resolved before this await because document.currentScript is only set
// while the script executes synchronously.
function whenBodyReady(): Promise<void> {
  if (document.body) return Promise.resolve();
  return new Promise((resolve) => {
    document.addEventListener("DOMContentLoaded", () => resolve(), { once: true });
  });
}

async function bootstrap(): Promise<void> {
  const scriptTag = findScriptTag();

  if (!scriptTag) {
    console.error("KLAI_WIDGET: Could not find script tag with data-widget-id");
    return;
  }

  const mode = scriptTag.getAttribute("data-mode") ?? "bubble";
  if (mode === "preview") return;

  const widgetId = scriptTag.getAttribute("data-widget-id");
  if (!widgetId) {
    console.error("KLAI_WIDGET: data-widget-id attribute is missing or empty");
    return;
  }

  const locale = scriptTag.getAttribute("data-locale") ?? undefined;
  const containerSelector = scriptTag.getAttribute("data-container");
  const clientSessionId = getInitialConversationSessionId(widgetId);

  // Presence of data-fresh-config disables reuse of a cached session-token
  // mint: the portal's own widget-test page loads the same bundle, and an
  // admin who just saved changes must see the fresh config immediately
  // instead of whatever a real visitor cached (up to the reuse window).
  const reuseCachedSession = !scriptTag.hasAttribute("data-fresh-config");

  if (mode === "inline" && containerSelector) {
    // Inline mode has no facade: the chat window sits on the page
    // deliberately, so the config is fetched immediately as before.
    let config;
    try {
      config = await loadConfigAndInitStore(widgetId, locale, clientSessionId, reuseCachedSession);
    } catch (error) {
      logFetchError(error);
      return;
    }

    setChatOpen(true);
    // Inline mode: mount ChatWindow directly into a page element, no shadow DOM
    await whenBodyReady();
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
        onClose: () => {},
        inline: true,
        conversationStarters: config.conversation_starters,
        hideDisclaimer: config.hide_disclaimer,
        welcomeMessage: config.welcome_message,
        bookingUrl: config.booking_url,
        aiDisclosureOverride: config.ai_disclosure_override,
        footerText: config.footer_text,
        nerdsEnabled: config.nerds?.enabled,
        nerdsBookingUrl: config.nerds?.booking_url,
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
  await whenBodyReady();
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
    ? `${widgetCss}\n:host { --klai-primary-color: ${previewColor}; --klai-primary-text-color: ${previewPrimaryTextColor(previewColor)}; }`
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
    const config = await loadConfigAndInitStore(
      widgetId,
      locale,
      clientSessionId,
      reuseCachedSession,
    );
    const cssVariables = cssVariableOverrides(config);
    if (cssVariables) {
      styleEl.textContent = `${widgetCss}\n:host { ${cssVariables} }`;
    }
  };

  render(() => WidgetFacade({ launch }), mountPoint);
}

// Bootstrap asynchronously — never blocks host page
void bootstrap();
