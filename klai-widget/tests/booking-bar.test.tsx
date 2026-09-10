/**
 * The permanent booking bar and the footer disclosure.
 *
 * Two shapes exist side by side and must not bleed into each other:
 *
 * - nerds integration ON  → the appointment lives in the conversation, so the
 *   always-on bar is gone, and "onze nerds" in the footer is always a working
 *   escape route: clicking it opens the same nerds booking panel the
 *   in-conversation appointment button opens.
 * - nerds integration OFF → nothing changed at all. Widgets that never had
 *   this integration must render byte-for-byte the same controls as before.
 */

import { render, fireEvent, cleanup, waitFor } from "@solidjs/testing-library";
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

  it("keeps the disclosure sentence's wording, with a working escape link", () => {
    const { container } = renderWindow({
      nerdsEnabled: true,
      nerdsBookingUrl: BOOKING_URL,
    });
    const disclaimer = container.querySelector(".klai-disclaimer")!;
    expect(disclaimer.textContent).toBe(
      t().nerdsDisclosureBefore + t().nerdsDisclosureLink + t().nerdsDisclosureAfter,
    );
    const link = disclaimer.querySelector("button.klai-disclaimer-link") as HTMLButtonElement;
    expect(link).not.toBeNull();
    expect(link.textContent).toBe(t().nerdsDisclosureLink);

    // Clicking it must always open the nerds panel — the visitor must
    // always be able to escape to a human, from the footer, not just from
    // an in-conversation offer.
    fireEvent.click(link);
    expect(container.querySelector(".klai-nerds-panel")).not.toBeNull();
  });

  it("keeps the nerds escape link even when hideDisclaimer white-labels the accuracy footer", () => {
    // hideDisclaimer only white-labels the generic accuracy wording. With
    // nerds active the permanent booking bar is already gone (the offer
    // lives in-conversation instead), so this link is the visitor's only
    // remaining permanent route to a human — it must survive the toggle.
    const { container } = renderWindow({
      nerdsEnabled: true,
      nerdsBookingUrl: BOOKING_URL,
      hideDisclaimer: true,
    });
    const link = container.querySelector("button.klai-disclaimer-link");
    expect(link).not.toBeNull();
    expect(link!.textContent).toBe(t().nerdsDisclosureLink);
  });

  it("hides the AI introduction while preserving the footer", () => {
    const { container } = renderWindow({
      hideDisclaimer: true,
      aiDisclosureOverride: null,
      footerText: null,
    });
    expect(container.querySelector(".klai-hero-ai-disclosure")).toBeNull();
    expect(container.querySelector(".klai-disclaimer")!.textContent).toBe(t().disclaimer);
  });

  it("renders explicit introduction and footer text despite the legacy hide flag", async () => {
    const { container } = renderWindow({
      hideDisclaimer: true,
      nerdsEnabled: true,
      nerdsBookingUrl: BOOKING_URL,
      aiDisclosureOverride: "Je praat met de Voys-assistent.",
      footerText: `Onze AI kan fouten maken. Plan bij [onze nerds](${BOOKING_URL}).`,
    });
    await waitFor(() => {
      expect(container.querySelector(".klai-hero-ai-disclosure")!.textContent).toBe(
        "Je praat met de Voys-assistent.",
      );
    });
    const footer = container.querySelector(".klai-disclaimer")!;
    expect(footer.textContent).toBe("Onze AI kan fouten maken. Plan bij onze nerds.");
    const link = footer.querySelector("a")!;
    expect(link.getAttribute("href")).toBe(BOOKING_URL);
    expect(link.target).toBe("_blank");
    expect(link.rel).toBe("noopener noreferrer");
  });

  it("renders no introduction, footer, or oversized hero icon when both texts are explicitly blank", () => {
    const { container } = renderWindow({
      aiDisclosureOverride: "",
      footerText: "",
    });
    expect(container.querySelector(".klai-hero-ai-disclosure")).toBeNull();
    expect(container.querySelector(".klai-disclaimer")).toBeNull();
    expect(container.querySelector(".klai-hero-icon")).toBeNull();
  });

  it("does not execute HTML or script links supplied in a customer footer", () => {
    const { container } = renderWindow({
      footerText: 'Footer <img src=x onerror="alert(1)"> [unsafe](javascript:alert) <script>alert(1)</script>',
    });
    const footer = container.querySelector(".klai-disclaimer")!;
    expect(footer.textContent).toContain("Footer");
    expect(footer.querySelector("script, img, [onerror], a[href^='javascript:']")).toBeNull();
  });
});
