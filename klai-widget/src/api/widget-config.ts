export interface WidgetConfig {
  title: string;
  welcome_message: string;
  css_variables: Record<string, string>;
  chat_endpoint: string;
  session_token: string;
  session_expires_at: string;
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

export async function fetchWidgetConfig(widgetId: string): Promise<WidgetConfig> {
  let response: Response;

  try {
    response = await fetch(
      `${WIDGET_CONFIG_BASE_URL}/partner/v1/widget-config?id=${encodeURIComponent(widgetId)}`,
      {
        method: "GET",
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
  return data;
}
