/**
 * The in-chat appointment offer.
 *
 * The bot's offer used to be expressed as a permanent booking bar that stood
 * there whether or not the bot had offered anything. The escalation signal
 * moves it into the conversation: the button belongs to the one answer that
 * made the offer. Three things have to hold, and they are what this file
 * asserts — the signal renders the button, its absence renders nothing, and a
 * widget with no booking route renders nothing either (an offer with nowhere
 * to go is worse than no button at all).
 */

import { render, fireEvent, cleanup } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageList } from "../src/components/MessageList";
import { normalizeEscalation } from "../src/api/chat-stream";
import type { Message } from "../src/api/chat-stream";
import { t } from "../src/i18n/labels";

afterEach(cleanup);

const OFFER: Message = {
  role: "assistant",
  content: "Dit vind ik niet terug in onze helpartikelen.",
  escalation: { appointment: true },
};

const PLAIN: Message = {
  role: "assistant",
  content: "Je reset het wachtwoord via Instellingen > Beveiliging.",
};

function renderList(messages: Message[], onAppointment?: () => void) {
  return render(() => (
    <MessageList
      messages={messages}
      isStreaming={false}
      error={null}
      onAppointment={onAppointment}
    />
  ));
}

describe("appointment offer button", () => {
  it("renders under the answer that carries the signal", () => {
    const onAppointment = vi.fn();
    const { getByText } = renderList([PLAIN, OFFER], onAppointment);

    const button = getByText(t().bookingButton);
    expect(button.tagName).toBe("BUTTON");

    fireEvent.click(button);
    expect(onAppointment).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when the answer carries no signal", () => {
    const { queryByText } = renderList([PLAIN], vi.fn());
    expect(queryByText(t().bookingButton)).toBeNull();
  });

  it("renders once, on the offering message only", () => {
    const { queryAllByText } = renderList([OFFER, PLAIN, PLAIN], vi.fn());
    expect(queryAllByText(t().bookingButton)).toHaveLength(1);
  });

  it("renders nothing when the widget has no booking route", () => {
    // No onAppointment = no nerds integration configured. The signal may
    // arrive anyway; the widget must not promise a person it cannot reach.
    const { queryByText } = renderList([OFFER]);
    expect(queryByText(t().bookingButton)).toBeNull();
  });

  it("ignores an escalation that does not literally offer an appointment", () => {
    const message: Message = {
      ...OFFER,
      escalation: { appointment: false },
    };
    const { queryByText } = renderList([message], vi.fn());
    expect(queryByText(t().bookingButton)).toBeNull();
  });
});

describe("normalizeEscalation", () => {
  it("accepts only a literal appointment: true", () => {
    expect(normalizeEscalation({ appointment: true })).toEqual({ appointment: true });
  });

  it("rejects everything else", () => {
    for (const raw of [
      undefined,
      null,
      {},
      { appointment: false },
      { appointment: "true" },
      { appointment: 1 },
      "appointment",
      [],
    ]) {
      expect(normalizeEscalation(raw)).toBeNull();
    }
  });
});
