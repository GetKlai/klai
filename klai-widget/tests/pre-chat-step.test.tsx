import { cleanup, fireEvent, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatWindow } from "../src/components/ChatWindow";
import { initLabels } from "../src/i18n/labels";

afterEach(() => {
  cleanup();
  vi.resetModules();
});

/**
 * The pre-chat step takes the hero's place when the widget asks for contact
 * details, so the visitor reads why we want them before anything else is on
 * screen. Declining must leave a usable chat: a visitor who does not want to
 * be mailed still gets to ask their question.
 */
describe("pre-chat step", () => {
  it("replaces the hero and the composer until the visitor answers or declines", () => {
    initLabels("nl");
    const { container } = render(() => (
      <ChatWindow title="Voys Help NL" onClose={() => {}} collectUserInfo manageHandoffStream={false} />
    ));

    const step = container.querySelector(".klai-identity-step");
    expect(step).not.toBeNull();
    expect(step!.querySelector(".klai-identity-title")?.textContent).toBe("Voordat we beginnen");
    expect(container.querySelector(".klai-hero")).toBeNull();
    expect(container.querySelector(".klai-compose-row")).toBeNull();

    const start = step!.querySelector(".klai-identity-btn") as HTMLButtonElement;
    expect(start.disabled).toBe(true);

    fireEvent.click(step!.querySelector(".klai-clear-identity") as HTMLButtonElement);

    expect(container.querySelector(".klai-identity-step")).toBeNull();
    expect(container.querySelector(".klai-compose-row")).not.toBeNull();
  });

  it("stays out of the way when the widget does not ask for contact details", () => {
    initLabels("nl");
    const { container } = render(() => (
      <ChatWindow title="Voys Help NL" onClose={() => {}} manageHandoffStream={false} />
    ));

    expect(container.querySelector(".klai-identity-step")).toBeNull();
    expect(container.querySelector(".klai-compose-row")).not.toBeNull();
  });
});
