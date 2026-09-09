/**
 * The permanent booking bar and the footer disclosure.
 *
 * Two shapes exist side by side and must not bleed into each other:
 *
 * - nerds integration ON  → the appointment lives in the conversation, so the
 *   always-on bar is gone and "onze nerds" in the footer is a disclaimer
 *   sentence, plain text, not a second route to the same booking module.
 * - nerds integration OFF → nothing changed at all. Widgets that never had
 *   this integration must render byte-for-byte the same controls as before.
 */

import { render, cleanup } from "@solidjs/testing-library";
import { afterEach, describe, expect, it } from "vitest";

import { ChatWindow } from "../src/components/ChatWindow";
import { t } from "../src/i18n/labels";

afterEach(cleanup);

const BOOKING_URL = "https://booking.example.com/afspraak";

function renderWindow(props: Record<string, unknown> = {}) {
  return render(() => (
    <ChatWindow
      title="Help"
      onClose={() => {}}
      inline
      manageHandoffStream={false}
      bookingUrl={BOOKING_URL}
      {...props}
    />
  ));
}

describe("permanent booking bar", () => {
  it("stays exactly as before for a widget without the nerds integration", () => {
    const { container } = renderWindow();
    const bar = container.querySelector(".klai-booking-bar");
    expect(bar).not.toBeNull();
    const link = bar!.querySelector("a.klai-booking-btn") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(BOOKING_URL);
    expect(link.textContent).toBe(t().bookingButton);
    // And the plain accuracy footer, not the client disclosure sentence.
    expect(container.querySelector(".klai-disclaimer")!.textContent).toBe(t().disclaimer);
  });

  it("disappears once the nerds integration is on", () => {
    const { container } = renderWindow({
      nerdsEnabled: true,
      nerdsBookingUrl: BOOKING_URL,
    });
    expect(container.querySelector(".klai-booking-bar")).toBeNull();
  });

  it("keeps the disclosure sentence as text, with no second appointment button", () => {
    const { container } = renderWindow({
      nerdsEnabled: true,
      nerdsBookingUrl: BOOKING_URL,
    });
    const disclaimer = container.querySelector(".klai-disclaimer")!;
    expect(disclaimer.textContent).toBe(
      t().nerdsDisclosureBefore + t().nerdsDisclosureLink + t().nerdsDisclosureAfter,
    );
    expect(disclaimer.querySelector("button")).toBeNull();
    expect(disclaimer.querySelector("a")).toBeNull();
  });
});
