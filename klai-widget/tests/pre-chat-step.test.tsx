import { cleanup, fireEvent, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WidgetConfig } from "../src/api/widget-config";
import { ChatWindow } from "../src/components/ChatWindow";
import { initLabels } from "../src/i18n/labels";
import { setChatState } from "../src/store/chat";

function configWith(retention_days?: number): WidgetConfig {
  return {
    title: "Help",
    welcome_message: "Hi",
    css_variables: {},
    chat_endpoint: "https://api.example.test",
    session_token: "test-token",
    session_expires_at: new Date().toISOString(),
    retention_days,
  };
}

function noticeFor(locale: "nl" | "en", retentionDays?: number): string {
  initLabels(locale);
  setChatState("config", configWith(retentionDays));
  const { container } = render(() => (
    <ChatWindow title="Help" onClose={() => {}} collectUserInfo manageHandoffStream={false} />
  ));
  return container.querySelector(".klai-identity-help")!.textContent!;
}

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

  it("states the organisation's retention term in the visitor's language", () => {
    expect(noticeFor("nl", 90)).toBe(
      "We bewaren dit gesprek 90 dagen om ons systeem beter te maken. Je e-mailadres gebruiken we om te zien bij welke klant het gesprek hoort, zodat we contact met je kunnen opnemen als dat nodig is.",
    );
    cleanup();
    expect(noticeFor("en", 90)).toBe(
      "We keep this conversation for 90 days to improve our system. We use your e-mail address to see which customer the conversation belongs to, so we can contact you if needed.",
    );
  });

  it("uses the singular for a term of one day", () => {
    expect(noticeFor("nl", 1)).toContain("We bewaren dit gesprek 1 dag om");
    cleanup();
    expect(noticeFor("en", 1)).toContain("We keep this conversation for 1 day to");
  });

  it("drops the term, and never shows a placeholder, when an older config response carries none", () => {
    for (const locale of ["nl", "en"] as const) {
      const text = noticeFor(locale);
      expect(text).not.toMatch(/[{}]/);
      expect(text).toMatch(
        locale === "nl"
          ? /^We bewaren dit gesprek om ons systeem beter te maken\. /
          : /^We keep this conversation to improve our system\. /,
      );
      cleanup();
    }
  });
});
