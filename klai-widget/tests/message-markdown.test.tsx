import { cleanup, render } from "@solidjs/testing-library";
import { afterEach, describe, expect, it } from "vitest";
import { MessageList } from "../src/components/MessageList";

afterEach(cleanup);

function renderAnswer(content: string) {
  const { container } = render(() => (
    <MessageList
      messages={[{ role: "assistant", content, sources: [
        { label: "1", title: "Belplan toevoegen", url: "https://help.voys.nl/belplan" },
      ] }]}
      isStreaming={false}
      error={null}
    />
  ));
  return container.querySelector(".klai-markdown")!;
}

describe("help answer Markdown", () => {
  it("keeps the reported call-plan instructions as separate paragraphs", () => {
    const answer = renderAnswer([
      "Ga naar **Belplan toevoegen**.",
      "Klik op **Belplan toevoegen**.",
      "Geef het telefoonnummer op waarvoor je een belplan wilt aanmaken.",
    ].join("\n\n"));
    expect(answer.querySelectorAll(":scope > p")).toHaveLength(3);
    expect(answer.querySelectorAll("strong")).toHaveLength(2);
  });

  it("preserves lists and code while keeping source links safe", () => {
    const answer = renderAnswer(
      "1. Open **Belplan toevoegen**.\n2. Vul de gegevens in.\n\n" +
      "- Naam\n- Nummer\n\n```html\n<script>alert(1)</script>\n```\n\n" +
      "Lees (1).\n\n[Onveilig](javascript:alert(1))<img src=x onerror=alert(1)>",
    );
    expect(answer.querySelectorAll("ol > li")).toHaveLength(2);
    expect(answer.querySelectorAll("ul > li")).toHaveLength(2);
    expect(answer.querySelector("pre code")?.textContent).toContain("<script>alert(1)</script>");
    expect(answer.querySelector("a.klai-citation")?.getAttribute("href")).toBe("https://help.voys.nl/belplan");
    expect(answer.querySelector('script, [onerror], a[href^="javascript:"]')).toBeNull();
  });
});
