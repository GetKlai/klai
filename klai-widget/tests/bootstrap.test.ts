import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Customers paste the embed snippet wherever their CMS puts third-party
// scripts, often in <head>. The bundle then runs before <body> is parsed.

function embedScriptInHead(): HTMLScriptElement {
  const script = document.createElement("script");
  script.src = "https://my.getklai.com/widget/klai-chat.js";
  script.setAttribute("data-widget-id", "wgt_test");
  document.head.appendChild(script);
  return script;
}

describe("widget bootstrap", () => {
  let body: HTMLElement;
  let unhandled: unknown[];
  const onUnhandled = (reason: unknown) => unhandled.push(reason);

  beforeEach(() => {
    vi.resetModules();
    unhandled = [];
    process.on("unhandledRejection", onUnhandled);
    body = document.body;
    document.documentElement.removeChild(body);
    embedScriptInHead();
  });

  afterEach(() => {
    process.off("unhandledRejection", onUnhandled);
    document.head.innerHTML = "";
    if (!document.body) document.documentElement.appendChild(body);
    document.body.innerHTML = "";
  });

  it("mounts the bubble once the body exists when the snippet sits in <head>", async () => {
    expect(document.body).toBeNull();

    await import("../src/main");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(unhandled).toEqual([]);

    document.documentElement.appendChild(body);
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.getElementById("klai-widget-root")).not.toBeNull();
  });
});
