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
  onAgentMessage: (content: string, id: number, agentName?: string) => void;
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
      summary: buildHandoffSummary(options.messages),
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
  lastEventId?: number;
}): Promise<void> {
  const params = new URLSearchParams();
  if (options.lastEventId && options.lastEventId > 0) {
    params.set("last_event_id", String(options.lastEventId));
  }
  const path = `/partner/v1/widget-handoffs/hubspot/events${params.toString() ? `?${params.toString()}` : ""}`;
  await fetchEventSource(endpoint(path), {
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
          agent_name?: string | null;
        };
        if (
          payload.type === "agent_message" ||
          payload.direction === "agent"
        ) {
          const content = typeof payload.content === "string" ? payload.content : "";
          if (content) {
            const agentName =
              typeof payload.agent_name === "string" && payload.agent_name.trim()
                ? payload.agent_name.trim()
                : undefined;
            options.callbacks.onAgentMessage(content, Number(payload.id || 0), agentName);
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

function buildHandoffSummary(messages: Message[]): string {
  const relevant = messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map((message) => ({
      role: message.role,
      content: message.content.trim(),
    }))
    .filter((message) => message.content.length > 0);

  const lastUser = [...relevant].reverse().find((message) => message.role === "user");
  const lastAssistant = [...relevant].reverse().find((message) => message.role === "assistant");
  const parts = ["Bezoeker vraagt om live hulp vanuit de Klai widget."];
  if (lastUser) {
    parts.push(`Laatste vraag van bezoeker: ${lastUser.content.slice(0, 600)}`);
  }
  if (lastAssistant) {
    parts.push(`Laatste Klai antwoord: ${lastAssistant.content.slice(0, 600)}`);
  }
  return parts.join("\n");
}
