export interface WidgetConfig {
  title: string;
  welcome_message: string;
  css_variables: Record<string, string>;
  chat_endpoint: string;
  session_token: string;
  session_expires_at: string;
  // TWD-style empty-state chips. Admin-configured, 0-6 entries.
  conversation_starters?: string[];
  // Legacy toggle, used only while no explicit introduction text is stored.
  hide_disclaimer?: boolean;
  footer_text?: string | null;
  // INTERIM appointment redirect — remove once the chat booking API
  // integration lands. Booking-module URL of the support partner, rendered
  // as the "schedule an appointment" button. The server only delivers
  // absolute http(s) values (partner.py _widget_booking_url), so this can
  // go straight into an href. Unset/empty → no button, current behaviour.
  booking_url?: string;
  // Nerds booking panel (Voys-specific support-partner integration). The
  // server only enables it when the toggle is on AND booking_url passed
  // the absolute http(s) gate (partner.py _widget_nerds_integration), so
  // an enabled block always carries a URL safe to use as an iframe src.
  // Absent/disabled → the widget renders exactly as it did before this
  // integration existed.
  nerds?: {
    enabled?: boolean;
    booking_url?: string;
  };
  // TWD-pattern: header avatar + empty-state hero need the bot name.
  // description is admin-only scope/behaviour config (see partner.py) and
  // is never rendered to visitors — kept here only as a locale-detection
  // sample in main.ts.
  name?: string;
  description?: string;
  // Explicit text, including an empty string, overrides legacy behaviour.
  // null/missing keeps the existing default introduction.
  ai_disclosure_override?: string | null;
  // Display toggles: show citation list / meta block under each
  // assistant message. Admin-controlled in the Vormgeving tab.
  show_sources?: boolean;
  show_meta?: boolean;
  page_context_enabled?: boolean;
  handoff?: {
    hubspot?: {
      enabled?: boolean;
    };
  };
  theme?: "light" | "dark";
  collect_user_info?: boolean;
  widget_position?: "left" | "right";
}

export type KlaiWidgetErrorCode =
  | "KLAI_WIDGET_NOT_FOUND"
  | "KLAI_WIDGET_ORIGIN_NOT_ALLOWED"
  | "KLAI_WIDGET_NETWORK_ERROR"
  | "KLAI_WIDGET_UNAUTHORIZED"
  | "KLAI_WIDGET_SERVER_ERROR";

export class KlaiWidgetError extends Error {
  public readonly code: KlaiWidgetErrorCode;
  public readonly status?: number;

  constructor(code: KlaiWidgetErrorCode, message: string, status?: number) {
    super(message);
    this.name = "KlaiWidgetError";
    this.code = code;
    this.status = status;
  }
}

const WIDGET_CONFIG_BASE_URL =
  typeof __WIDGET_CONFIG_BASE_URL__ !== "undefined"
    ? __WIDGET_CONFIG_BASE_URL__
    : "https://api.getklai.com";

declare const __WIDGET_CONFIG_BASE_URL__: string;

// Every successful call to /partner/v1/widget-config mints a fresh session
// token server-side, and that mint path is rate-limited per widget (10/min).
// Reopening the chat on the next help article therefore burned the whole
// customer site's budget on tokens that were still valid for an hour. The
// complete response is cached under a per-widget localStorage key. The
// backend stamps the mint request's X-Klai-Widget-Session-Id into the JWT
// as `jti` and derives the audit/handoff session key from it, so a cached
// token only belongs to the conversation it was minted for: reuse is bound
// to the stored sessionId and a mint for any other conversation overwrites
// the entry. Reuse is capped at 15 minutes after mint and always ends 60s
// before the server-side expiry. Staleness trade-off: portal edits to
// settings only reach a visitor after that window — bounded and cosmetic,
// the alternative is the mint storm this fixes.
interface CachedWidgetSession {
  /** Conversation the token was minted for; becomes the JWT `jti`. */
  sessionId: string;
  mintedAtMs: number;
  config: WidgetConfig;
}

const SESSION_CACHE_MARGIN_MS = 60_000;
const SESSION_CACHE_REUSE_MS = 15 * 60_000;

function sessionCacheKey(widgetId: string): string {
  return `klai-widget:${widgetId}:config-session:v1`;
}

function readCachedWidgetSession(
  widgetId: string,
  sessionId: string | undefined,
): WidgetConfig | null {
  if (!sessionId) return null;
  try {
    const raw = window.localStorage.getItem(sessionCacheKey(widgetId));
    if (!raw) return null;
    const cached = JSON.parse(raw) as Partial<CachedWidgetSession>;
    const config = cached.config;
    const expiresAtMs = config ? Date.parse(config.session_expires_at) : Number.NaN;
    if (
      !config ||
      typeof config !== "object" ||
      typeof config.session_token !== "string" ||
      !config.session_token ||
      !Number.isFinite(cached.mintedAtMs) ||
      !Number.isFinite(expiresAtMs)
    ) {
      return null;
    }
    // Minted for another conversation: a hit would replay the old `jti`.
    // Leave the entry; only a mint for its own session may overwrite it.
    if (cached.sessionId !== sessionId) return null;
    const now = Date.now();
    if (
      now - (cached.mintedAtMs as number) >= SESSION_CACHE_REUSE_MS ||
      now >= expiresAtMs - SESSION_CACHE_MARGIN_MS
    ) {
      window.localStorage.removeItem(sessionCacheKey(widgetId));
      return null;
    }
    return config;
  } catch {
    // Private mode or corrupted entry: fall back to minting on every open.
    return null;
  }
}

/** Drop the cached mint after its token was rejected (401 from the chat
 * endpoint), so it can never be replayed on reload. Only removes when the
 * entry still holds that exact token — a newer mint (another tab refreshed
 * it) must survive. */
export function clearCachedWidgetSession(widgetId: string, rejectedToken: string): void {
  try {
    const raw = window.localStorage.getItem(sessionCacheKey(widgetId));
    if (!raw) return;
    const cached = JSON.parse(raw) as Partial<CachedWidgetSession>;
    if (cached.config?.session_token !== rejectedToken) return;
    window.localStorage.removeItem(sessionCacheKey(widgetId));
  } catch {
    // Same silent fallback as the read path.
  }
}

function writeCachedWidgetSession(
  widgetId: string,
  sessionId: string,
  config: WidgetConfig,
): void {
  if (!config.session_token || !Number.isFinite(Date.parse(config.session_expires_at))) return;
  try {
    const cached: CachedWidgetSession = {
      sessionId,
      mintedAtMs: Date.now(),
      config,
    };
    window.localStorage.setItem(sessionCacheKey(widgetId), JSON.stringify(cached));
  } catch {
    // Storage failures (private mode, quota) keep today's behaviour.
  }
}

export async function fetchWidgetConfig(
  widgetId: string,
  options: { sessionId?: string; reuseCachedSession?: boolean } = {},
): Promise<WidgetConfig> {
  if (options.reuseCachedSession) {
    const cached = readCachedWidgetSession(widgetId, options.sessionId);
    if (cached) return cached;
  }

  let response: Response;

  try {
    const params = new URLSearchParams({ id: widgetId });
    const headers: Record<string, string> = {};
    if (options.sessionId) {
      headers["X-Klai-Widget-Session-Id"] = options.sessionId;
    }
    response = await fetch(
      `${WIDGET_CONFIG_BASE_URL}/partner/v1/widget-config?${params.toString()}`,
      {
        method: "GET",
        headers,
        // No credentials — Origin header sent automatically by browser
        // No Authorization header — wgt_... ID is the public identifier
      }
    );
  } catch {
    throw new KlaiWidgetError(
      "KLAI_WIDGET_NETWORK_ERROR",
      "Network error while fetching widget config"
    );
  }

  if (response.status === 403) {
    throw new KlaiWidgetError(
      "KLAI_WIDGET_ORIGIN_NOT_ALLOWED",
      "Origin not allowed for this widget",
      403
    );
  }

  if (response.status === 404) {
    throw new KlaiWidgetError(
      "KLAI_WIDGET_NOT_FOUND",
      `Widget with id '${widgetId}' not found`,
      404
    );
  }

  if (response.status === 401) {
    throw new KlaiWidgetError(
      "KLAI_WIDGET_UNAUTHORIZED",
      "Unauthorized",
      401
    );
  }

  if (!response.ok) {
    throw new KlaiWidgetError(
      "KLAI_WIDGET_SERVER_ERROR",
      `Server error: ${response.status}`,
      response.status
    );
  }

  const data = (await response.json()) as WidgetConfig;

  // Resolve relative chat_endpoint against the API base URL
  if (data.chat_endpoint && data.chat_endpoint.startsWith("/")) {
    data.chat_endpoint = `${WIDGET_CONFIG_BASE_URL}${data.chat_endpoint}`;
  }

  // Every mint refreshes the entry, so a reload — or the launch after a
  // conversation switch or a 401 re-mint — reuses the token minted for the
  // conversation that is actually active.
  if (options.sessionId) {
    writeCachedWidgetSession(widgetId, options.sessionId, data);
  }

  return data;
}
