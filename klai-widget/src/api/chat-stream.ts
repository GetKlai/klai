import { fetchEventSource } from "@microsoft/fetch-event-source";
import { fetchWidgetConfig, KlaiWidgetError } from "./widget-config";

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (error: KlaiWidgetError | Error) => void;
}

interface ChatStreamOptions {
  endpoint: string;
  token: string;
  messages: Message[];
  widgetId: string;
  callbacks: StreamCallbacks;
  abortController?: AbortController;
}

class RetriableError extends Error {}
class FatalError extends Error {}

export async function streamChat(options: ChatStreamOptions): Promise<void> {
  const { endpoint, token, messages, widgetId, callbacks, abortController } = options;
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
                delta?: { content?: string };
                finish_reason?: string;
              }>;
            };
            const content = parsed.choices?.[0]?.delta?.content;
            if (content) {
              callbacks.onToken(content);
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
