/**
 * Where a link in the tenant's footer text takes the visitor.
 *
 * `footerLinksInWidget` is the whole difference:
 *
 * - ON  → a plain left-click opens the widget's own panel, so the visitor
 *   keeps the conversation and can come back to it.
 * - OFF → nothing changed at all: the anchor still opens a new browser tab,
 *   which is what every widget without the flag has always done.
 */

import { render, fireEvent, cleanup } from "@solidjs/testing-library";
import { afterEach, describe, expect, it } from "vitest";

import { ChatWindow } from "../src/components/ChatWindow";

afterEach(cleanup);

const FOOTER_TEXT = "Plan een afspraak met [onze nerds](https://example.com/agenda).";

function renderWindow(props: Record<string, unknown> = {}) {
  return render(() => (
    <ChatWindow
      title="Help"
      onClose={() => {}}
      inline
      manageHandoffStream={false}
      footerText={FOOTER_TEXT}
      {...props}
    />
  ));
}

function footerAnchor(container: HTMLElement): HTMLAnchorElement {
  return container.querySelector(".klai-disclaimer a") as HTMLAnchorElement;
}

describe("footer link panel", () => {
  it("opens the in-widget panel on the footer link's url and title", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    footerAnchor(container).dispatchEvent(click);

    const panel = container.querySelector(".klai-nerds-panel");
    expect(panel).not.toBeNull();
    expect(panel!.querySelector("iframe.klai-nerds-frame")!.getAttribute("src")).toBe(
      "https://example.com/agenda",
    );
    expect(panel!.querySelector(".klai-nerds-panel-title")!.textContent).toBe("onze nerds");
    // The chat page must not be navigated away by the intercepted click.
    expect(click.defaultPrevented).toBe(true);
  });

  it("always offers the same url as a new-tab link and delegates no capabilities", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });
    fireEvent.click(footerAnchor(container));

    // A destination that refuses to be framed renders an error page we cannot
    // detect, so the escape hatch cannot depend on the load event.
    const escape = container.querySelector(
      ".klai-nerds-panel-actions a",
    ) as HTMLAnchorElement;
    expect(escape.href).toBe("https://example.com/agenda");
    expect(escape.target).toBe("_blank");
    // A tenant's own footer destination gets none of the booking module's
    // clipboard/payment/geolocation permissions.
    expect(container.querySelector("iframe.klai-nerds-frame")!.getAttribute("allow")).toBe("");
  });

  it("keeps opening a new tab when the flag is absent", () => {
    const { container } = renderWindow();
    const anchor = footerAnchor(container);
    fireEvent.click(anchor);

    expect(container.querySelector(".klai-nerds-panel")).toBeNull();
    expect(anchor.target).toBe("_blank");
    expect(anchor.rel).toBe("noopener noreferrer");
  });

  it("leaves a cmd-click to the browser even with the flag on", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });
    const click = new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      metaKey: true,
    });
    footerAnchor(container).dispatchEvent(click);

    expect(container.querySelector(".klai-nerds-panel")).toBeNull();
    expect(click.defaultPrevented).toBe(false);
  });
});
