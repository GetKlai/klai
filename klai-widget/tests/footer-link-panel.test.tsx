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

function panelBackButton(container: HTMLElement): HTMLButtonElement {
  return container.querySelector(
    ".klai-nerds-panel-actions button.klai-nerds-panel-back",
  ) as HTMLButtonElement;
}

// Solid assigns `inert` as a property, which is how modern browsers reflect
// it; this jsdom build does not implement the attribute at all, so the
// property is the only thing a unit test can observe.
function chatIsInert(container: HTMLElement): boolean {
  const body = container.querySelector(".klai-window-body") as HTMLElement & { inert?: boolean };
  return body.inert === true;
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

  // WCAG 2.4.3: the panel covers the chat, so the chat behind it must stop
  // taking keyboard focus and screen-reader attention, and focus has to move
  // into the panel that replaced it.
  it("moves focus to the panel's back button and marks the chat inert", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });
    fireEvent.click(footerAnchor(container));

    expect(document.activeElement).toBe(panelBackButton(container));
    expect(chatIsInert(container)).toBe(true);
  });

  it("returns focus to the composer and clears inert when the panel closes", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });
    fireEvent.click(footerAnchor(container));
    fireEvent.click(panelBackButton(container));

    expect(chatIsInert(container)).toBe(false);
    expect(document.activeElement).toBe(container.querySelector(".klai-textarea"));
  });

  it("keeps the focus the page gave the visitor while no panel ever opened", () => {
    const { container } = renderWindow({ footerLinksInWidget: true });

    expect(document.activeElement).toBe(document.body);
    expect(chatIsInert(container)).toBe(false);
  });
});
