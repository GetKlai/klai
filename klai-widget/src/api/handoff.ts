import { fetchEventSource } from "@microsoft/fetch-event-source";
import type { Message } from "./chat-stream";

const WIDGET_CONFIG_BASE_URL =
  typeof __WIDGET_CONFIG_BASE_URL__ !== "undefined"
    ? __WIDGET_CONFIG_BASE_URL__
    : "https://api.getklai.com";

declare const __WIDGET_CONFIG_BASE_URL__: string;

function endpoint(path: string): string {
  return `${WIDGET_CONFIG_BASE_URL}${path}`;
}

export interface HandoffEventCallbacks {
  onAgentMessage: (content: string, id: number) => void;
  onError: (error: Error) => void;
}

export async function startHubSpotHandoff(options: {
  token: string;
  messages: Message[];
}): Promise<void> {
  const response = await fetch(endpoint("/partner/v1/widget-handoffs/hubspot/start"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${options.token}`,
    },
    body: JSON.stringify({
      messages: options.messages
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) => ({
          role: message.role,
          content: message.content,
        })),
    }),
  });
  if (!response.ok) {
    throw new Error(`HubSpot handoff start failed: ${response.status}`);
  }
}

export async function sendHubSpotHandoffMessage(options: {
  token: string;
  content: string;
}): Promise<void> {
  const response = await fetch(endpoint("/partner/v1/widget-handoffs/hubspot/messages"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${options.token}`,
    },
    body: JSON.stringify({ content: options.content }),
  });
  if (!response.ok) {
    throw new Error(`HubSpot handoff message failed: ${response.status}`);
  }
}

export async function streamHubSpotHandoffEvents(options: {
  token: string;
  callbacks: HandoffEventCallbacks;
  abortController?: AbortController;
}): Promise<void> {
  await fetchEventSource(endpoint("/partner/v1/widget-handoffs/hubspot/events"), {
    method: "GET",
    headers: {
      Authorization: `Bearer ${options.token}`,
    },
    signal: options.abortController?.signal,
    onmessage: (event) => {
      if (event.event !== "message" || !event.data) {
        return;
      }
      try {
        const payload = JSON.parse(event.data) as {
          id?: number;
          type?: string;
          direction?: string;
          content?: string;
        };
        if (
          payload.type === "agent_message" ||
          payload.direction === "agent"
        ) {
          const content = typeof payload.content === "string" ? payload.content : "";
          if (content) {
            options.callbacks.onAgentMessage(content, Number(payload.id || 0));
          }
        }
      } catch {
        // Ignore malformed events; the next heartbeat/message keeps the stream alive.
      }
    },
    onerror: (error) => {
      options.callbacks.onError(error instanceof Error ? error : new Error("Handoff stream failed"));
      throw error;
    },
  });
}
