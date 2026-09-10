import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatBubble } from "../src/components/ChatBubble";
import { ChatWindow } from "../src/components/ChatWindow";
import { initLabels } from "../src/i18n/labels";

const widgetCss = readFileSync(join(process.cwd(), "src/styles/widget.css"), "utf8");

afterEach(() => {
  cleanup();
  document.head.innerHTML = "";
  document.querySelectorAll("#klai-widget-root").forEach((node) => node.remove());
  vi.resetModules();
});

describe("widget shell", () => {
  it("keeps deployed defaults while exposing scoped layout overrides", () => {
    initLabels("nl");
    const { container } = render(() => (
      <ChatWindow title="Voys Help NL" onClose={() => {}} manageHandoffStream={false} />
    ));
    const header = container.querySelector(".klai-header")!;
    const newConversation = header.querySelector(
      'button[aria-label="Nieuw gesprek"]',
    ) as HTMLButtonElement;

    expect(header.querySelector(".klai-header-status")?.textContent).toBe("Online");
    expect(newConversation.textContent).toContain("Nieuw gesprek");
    expect(container.querySelector(".klai-textarea")?.parentElement?.className).toBe(
      "klai-compose-row",
    );

    const style = document.createElement("style");
    style.textContent = widgetCss;
    document.head.append(style);
    container.classList.add("klai-inline-root");
    container.style.setProperty("--klai-primary-color", "#270697");
    expect(getComputedStyle(container).getPropertyValue("--klai-header-background").trim()).toBe(
      "var(--klai-primary-color)",
    );

    container.style.setProperty("--klai-header-background", "#ffffff");
    expect(getComputedStyle(container).getPropertyValue("--klai-header-background").trim()).toBe(
      "#ffffff",
    );
  });

  it("keeps launcher artwork unfilled and centers the header actions", () => {
    const { container } = render(() => <ChatBubble />);
    const bubble = container.querySelector(".klai-bubble") as HTMLButtonElement;
    const icon = bubble.querySelector("svg") as SVGElement;

    expect(icon.getAttribute("stroke")).toBe("currentColor");
    expect(widgetCss).toMatch(/\.klai-bubble\s*{[^}]*color:\s*var\(--klai-primary-text-color\)/s);
    expect(widgetCss).toMatch(/\.klai-bubble svg\s*{[^}]*fill:\s*none/s);
    expect(widgetCss).toMatch(/\.klai-header-actions\s*{[^}]*align-self:\s*center/s);
    expect(widgetCss).toMatch(/\.klai-close-btn\s*{[^}]*align-self:\s*center/s);
  });

  it("renders the Voys facade icon white before fetching config", async () => {
    const script = document.createElement("script");
    script.src = "https://my.getklai.com/widget/klai-chat.js";
    script.setAttribute("data-widget-id", "wgt_voys");
    script.setAttribute("data-primary-color", "#270697");
    document.head.appendChild(script);

    await import("../src/main");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const root = document.getElementById("klai-widget-root")!;
    const style = root.shadowRoot!.querySelector("style")!;
    expect(style.textContent).toContain("--klai-primary-color: #270697");
    expect(style.textContent).toContain("--klai-primary-text-color: #ffffff");
    expect(root.shadowRoot!.querySelector("button.klai-bubble svg")).not.toBeNull();
  });
});
