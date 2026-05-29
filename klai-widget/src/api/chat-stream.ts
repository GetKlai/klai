import { fetchEventSource } from "@microsoft/fetch-event-source";
import { fetchWidgetConfig, KlaiWidgetError } from "./widget-config";

export interface Message {
  role: "user" | "assistant" | "agent";
  content: string;
  sources?: MessageSource[];
  id?: number;
  agentName?: string;
  activity?: AgentActivity[];
}

export interface MessageSource {
  label: string;
  title: string;
  /** Empty when the source is an uploaded/private document without a public URL. */
  url: string;
}

export interface PageContext {
  url: string;
  path: string;
  title?: string;
  referrer?: string;
  excerpt?: string;
}

export interface AgentActivity {
  step: string;
  label: string;
  detail?: string;
  count?: number;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onSources?: (sources: MessageSource[]) => void;
  onActivity?: (activity: AgentActivity[]) => void;
  onDone: () => void;
  onError: (error: KlaiWidgetError | Error) => void;
}

interface ChatStreamOptions {
  endpoint: string;
  token: string;
  messages: Message[];
  widgetId: string;
  pageContext?: PageContext;
  callbacks: StreamCallbacks;
  abortController?: AbortController;
}

class RetriableError extends Error {}
class FatalError extends Error {}

const INVALID_SOURCE_VALUES = new Set(["", "undefined", "null", "none"]);
const INVALID_SOURCE_PLACEHOLDERS = new Set(["undefined", "null", "none"]);
const MAX_PAGE_CONTEXT_VALUE_CHARS = 2048;
const MAX_PAGE_EXCERPT_CHARS = 2000;

function cleanContextValue(
  value: string | undefined,
  maxChars = MAX_PAGE_CONTEXT_VALUE_CHARS
): string | undefined {
  const cleaned = value?.replace(/\s+/g, " ").trim();
  if (!cleaned) {
    return undefined;
  }
  return cleaned.slice(0, maxChars);
}

function collectPageExcerpt(): string | undefined {
  const root =
    document.querySelector<HTMLElement>("main, article, [role='main']") ??
    document.body;
  if (!root) {
    return undefined;
  }

  const clone = root.cloneNode(true) as HTMLElement;
  const ignoredSelectors = [
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "header",
    "footer",
    "form",
    "input",
    "textarea",
    "select",
    "button",
    "iframe",
    "#klai-widget-root",
    ".klai-window",
    ".klai-inline-root",
  ].join(", ");
  clone.querySelectorAll(ignoredSelectors).forEach((node) => node.remove());

  return cleanContextValue(clone.innerText, MAX_PAGE_EXCERPT_CHARS);
}

function cleanContextUrl(rawUrl: string | undefined): string | undefined {
  if (!rawUrl) {
    return undefined;
  }
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return undefined;
    }
    url.search = "";
    url.hash = "";
    return url.toString().slice(0, MAX_PAGE_CONTEXT_VALUE_CHARS);
  } catch {
    return undefined;
  }
}

export function collectPageContext(): PageContext | undefined {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return undefined;
  }

  try {
    const currentUrl = cleanContextUrl(window.location.href);
    if (!currentUrl) {
      return undefined;
    }

    return {
      url: currentUrl,
      path: window.location.pathname.slice(0, 512),
      title: cleanContextValue(document.title),
      excerpt: collectPageExcerpt(),
    };
  } catch {
    return undefined;
  }
}

export function normalizeSourceUrl(raw: unknown): string | null {
  if (typeof raw !== "string") {
    return null;
  }

  const value = raw.trim();
  if (INVALID_SOURCE_VALUES.has(value.toLowerCase()) || !/^https?:\/\//i.test(value)) {
    return null;
  }

  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null;
    }
    if (INVALID_SOURCE_PLACEHOLDERS.has(url.hostname.toLowerCase())) {
      return null;
    }
    const placeholderPath = url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
    if (INVALID_SOURCE_PLACEHOLDERS.has(placeholderPath)) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

export function normalizeMessageSources(rawSources: unknown): MessageSource[] {
  if (!Array.isArray(rawSources)) {
    return [];
  }

  const normalized: MessageSource[] = [];
  const seenLabels = new Set<string>();

  for (const rawSource of rawSources) {
    if (!rawSource || typeof rawSource !== "object") {
      continue;
    }
    const source = rawSource as Partial<MessageSource>;
    const label = typeof source.label === "string" ? source.label.trim() : "";
    const url = normalizeSourceUrl(source.url) ?? "";
    if (!/^\d+$/.test(label) || seenLabels.has(label)) {
      continue;
    }
    const title = typeof source.title === "string" && source.title.trim() ? source.title.trim() : `Source ${label}`;
    normalized.push({ label, title, url });
    seenLabels.add(label);
  }

  return normalized;
}

export function normalizeAgentActivity(rawActivity: unknown): AgentActivity[] {
  if (!Array.isArray(rawActivity)) {
    return [];
  }

  const normalized: AgentActivity[] = [];
  const seenSteps = new Set<string>();

  for (const rawItem of rawActivity) {
    if (!rawItem || typeof rawItem !== "object") {
      continue;
    }
    const item = rawItem as Partial<AgentActivity>;
    const step = typeof item.step === "string" ? item.step.trim() : "";
    const label = typeof item.label === "string" ? item.label.trim() : "";
    if (!step || !label || seenSteps.has(step)) {
      continue;
    }
    const detail = typeof item.detail === "string" && item.detail.trim() ? item.detail.trim() : undefined;
    const count = typeof item.count === "number" && Number.isFinite(item.count) ? item.count : undefined;
    normalized.push({ step, label, detail, count });
    seenSteps.add(step);
  }

  return normalized;
}

export async function streamChat(options: ChatStreamOptions): Promise<void> {
  const { endpoint, token, messages, widgetId, pageContext, callbacks, abortController } = options;
  let currentToken = token;
  let retried = false;

  const doStream = async (authToken: string): Promise<void> => {
    return new Promise<void>((resolve, reject) => {
      fetchEventSource(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          messages,
          stream: true,
          page_context: pageContext,
        }),
        signal: abortController?.signal,
        onopen: async (response) => {
          if (response.ok) {
            return;
          }
          if (response.status === 401) {
            if (!retried) {
              // Token expired — attempt refresh once
              throw new RetriableError("Token expired, will refresh");
            }
            // Second 401 after refresh — fatal
            throw new FatalError("Session token invalid after refresh");
          }
          if (response.status >= 400 && response.status < 500) {
            throw new FatalError(`Client error: ${response.status}`);
          }
          throw new RetriableError(`Server error: ${response.status}`);
        },
        onmessage: (event) => {
          if (event.data === "[DONE]") {
            callbacks.onDone();
            resolve();
            return;
          }
          try {
            const parsed = JSON.parse(event.data) as {
              choices?: Array<{
                delta?: { content?: string; sources?: unknown; activity?: unknown };
                finish_reason?: string;
              }>;
            };
            const delta = parsed.choices?.[0]?.delta;
            const content = delta?.content;
            if (content) {
              callbacks.onToken(content);
            }
            const sources = normalizeMessageSources(delta?.sources);
            if (sources.length > 0) {
              callbacks.onSources?.(sources);
            }
            const activity = normalizeAgentActivity(delta?.activity);
            if (activity.length > 0) {
              callbacks.onActivity?.(activity);
            }
            if (parsed.choices?.[0]?.finish_reason === "stop") {
              callbacks.onDone();
              resolve();
            }
          } catch {
            // Non-JSON data or empty event — skip
          }
        },
        onerror: (error) => {
          if (error instanceof FatalError) {
            reject(error);
            throw error; // Stop retrying
          }
          if (error instanceof RetriableError) {
            reject(error);
            throw error; // We handle retry ourselves
          }
          // Unexpected error — treat as fatal
          reject(error);
          throw error;
        },
        onclose: () => {
          resolve();
        },
        openWhenHidden: true,
      });
    });
  };

  try {
    await doStream(currentToken);
  } catch (error) {
    if (error instanceof RetriableError && !retried) {
      // Attempt token refresh once
      retried = true;
      try {
        const freshConfig = await fetchWidgetConfig(widgetId);
        currentToken = freshConfig.session_token;
        try {
          await doStream(currentToken);
        } catch (retryError) {
          const wrappedError =
            retryError instanceof KlaiWidgetError
              ? retryError
              : new KlaiWidgetError(
                  "KLAI_WIDGET_UNAUTHORIZED",
                  retryError instanceof Error ? retryError.message : "Stream failed after token refresh"
                );
          callbacks.onError(wrappedError);
        }
      } catch (refreshError) {
        const wrappedError =
          refreshError instanceof KlaiWidgetError
            ? refreshError
            : new KlaiWidgetError(
                "KLAI_WIDGET_NETWORK_ERROR",
                refreshError instanceof Error ? refreshError.message : "Failed to refresh session token"
              );
        callbacks.onError(wrappedError);
      }
    } else {
      const wrappedError =
        error instanceof KlaiWidgetError
          ? error
          : new KlaiWidgetError(
              "KLAI_WIDGET_NETWORK_ERROR",
              error instanceof Error ? error.message : "Unknown stream error"
            );
      callbacks.onError(wrappedError);
    }
  }
}
