import type { MessageRating } from "./chat-stream";

const WIDGET_CONFIG_BASE_URL =
  typeof __WIDGET_CONFIG_BASE_URL__ !== "undefined"
    ? __WIDGET_CONFIG_BASE_URL__
    : "https://api.getklai.com";

declare const __WIDGET_CONFIG_BASE_URL__: string;

export async function sendTurnFeedback(options: {
  token: string;
  turnId: string;
  /** null withdraws a previously set rating. */
  rating: MessageRating | null;
}): Promise<void> {
  const response = await fetch(`${WIDGET_CONFIG_BASE_URL}/partner/v1/widget/feedback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${options.token}`,
    },
    body: JSON.stringify({
      turn_id: options.turnId,
      rating: options.rating,
    }),
  });
  if (!response.ok) {
    throw new Error(`Widget feedback failed: ${response.status}`);
  }
}
