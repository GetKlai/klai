/// <reference types="vite/client" />
import { render } from "solid-js/web";
import { ChatBubble } from "./components/ChatBubble";
import { ChatWindow } from "./components/ChatWindow";
import { initStore } from "./store/chat";
import { fetchWidgetConfig, KlaiWidgetError } from "./api/widget-config";
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

  // Init i18n labels (browser locale or data-locale override)
  initLabels(locale);

  let config;
  try {
    config = await fetchWidgetConfig(widgetId);
  } catch (error) {
    if (error instanceof KlaiWidgetError) {
      console.error(error.code);
    } else {
      console.error("KLAI_WIDGET_NETWORK_ERROR");
    }
    return;
  }

  // Apply css_variables from config as custom properties overrides
  const cssVariableOverrides = Object.entries(config.css_variables)
    .map(([key, value]) => `${key}: ${value};`)
    .join(" ");

  // Initialize the store with config and widget ID
  initStore(widgetId, config);

  if (mode === "inline" && containerSelector) {
    // Inline mode: mount ChatWindow directly into a page element, no shadow DOM
    const target = document.querySelector(containerSelector);
    if (!target) {
      console.error(`KLAI_WIDGET: Container "${containerSelector}" not found`);
      return;
    }

    // Inject scoped styles into the page (no shadow DOM in inline mode)
    const styleEl = document.createElement("style");
    styleEl.textContent = cssVariableOverrides
      ? `${widgetCss}\n.klai-inline-root { ${cssVariableOverrides} }`
      : widgetCss;
    document.head.appendChild(styleEl);

    target.classList.add("klai-inline-root");

    render(
      () => ChatWindow({ title: config.title, onClose: () => {}, inline: true }),
      target as HTMLElement,
    );
  } else {
    // Bubble mode (default): Shadow DOM floating widget
    const container = document.createElement("div");
    container.setAttribute("id", "klai-widget-root");
    document.body.appendChild(container);

    const shadowRoot = container.attachShadow({ mode: "open" });

    const styleEl = document.createElement("style");
    styleEl.textContent = cssVariableOverrides
      ? `${widgetCss}\n:host { ${cssVariableOverrides} }`
      : widgetCss;
    shadowRoot.appendChild(styleEl);

    const mountPoint = document.createElement("div");
    shadowRoot.appendChild(mountPoint);

    render(() => ChatBubble(), mountPoint);
  }
}

// Bootstrap asynchronously — never blocks host page
void bootstrap();
