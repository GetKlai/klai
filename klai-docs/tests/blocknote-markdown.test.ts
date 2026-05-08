import { describe, expect, it } from "vitest";
import { blockNoteJsonToMarkdown } from "../lib/blocknote-markdown";

describe("blockNoteJsonToMarkdown", () => {
  it("renders BlockNote JSON blocks as markdown instead of raw JSON text", () => {
    const content = JSON.stringify([
      {
        type: "paragraph",
        content: [{ type: "text", text: "Start here.", styles: {} }],
        children: [],
      },
      {
        type: "heading",
        props: { level: 2 },
        content: [{ type: "text", text: "Create your account", styles: {} }],
        children: [],
      },
      {
        type: "paragraph",
        content: [
          { type: "text", text: "Go to ", styles: {} },
          {
            type: "link",
            href: "https://my.getklai.com",
            content: [{ type: "text", text: "my.getklai.com", styles: {} }],
          },
          { type: "text", text: " and sign up.", styles: {} },
        ],
        children: [],
      },
      {
        type: "numberedListItem",
        content: [
          { type: "text", text: "Open ", styles: {} },
          { type: "text", text: "Knowledge", styles: { bold: true } },
          { type: "text", text: ".", styles: {} },
        ],
        children: [],
      },
    ]);

    const markdown = blockNoteJsonToMarkdown(content);

    expect(markdown).toContain("Start here\\.");
    expect(markdown).toContain("## Create your account");
    expect(markdown).toContain("[my\\.getklai\\.com](https://my.getklai.com)");
    expect(markdown).toContain("1. Open **Knowledge**\\.");
    expect(markdown).not.toContain('"type":"paragraph"');
  });

  it("resolves BlockNote wikilinks through the page index", () => {
    const content = JSON.stringify([
      {
        type: "paragraph",
        content: [
          { type: "text", text: "Read ", styles: {} },
          {
            type: "wikilink",
            props: { pageId: "page-1", title: "Welcome" },
          },
          { type: "text", text: ".", styles: {} },
        ],
        children: [],
      },
    ]);

    const markdown = blockNoteJsonToMarkdown(
      content,
      [{ id: "page-1", slug: "welcome", title: "Welcome" }],
      "klai-help"
    );

    expect(markdown).toBe("Read [Welcome](/docs/klai-help/welcome)\\.");
  });

  it("falls back for non-JSON legacy markdown or invalid JSON", () => {
    expect(blockNoteJsonToMarkdown("## Legacy markdown")).toBeNull();
    expect(blockNoteJsonToMarkdown("[not json")).toBeNull();
  });
});
