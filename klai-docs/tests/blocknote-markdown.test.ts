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

  it("moves whitespace out of bold/italic/strike/underline/code runs so emphasis still renders", () => {
    const content = JSON.stringify([
      {
        type: "paragraph",
        content: [
          { type: "text", text: "If you are a Chat user, you have access to", styles: {} },
          { type: "text", text: " File upload", styles: { bold: true } },
          { type: "text", text: " and ", styles: {} },
          { type: "text", text: "URL crawler only", styles: { bold: true } },
          { type: "text", text: ". Trailing space ", styles: { italic: true } },
          { type: "text", text: "inline.", styles: {} },
        ],
        children: [],
      },
    ]);

    const markdown = blockNoteJsonToMarkdown(content);

    // Bold renders even with a leading space in the run (space moved outside the markers)
    expect(markdown).toContain("to **File upload** and **URL crawler only**");
    // Trailing space stays OUTSIDE the italic markers so emphasis still renders;
    // escapeMarkdown turns "." into "\.". Resulting fragment: _\. Trailing space_ inline\.
    expect(markdown).toContain("**URL crawler only**_\\. Trailing space_ inline\\.");
    // The original bug shape — opening marker glued to whitespace — must not reappear.
    // (Closing "**" followed by a space is fine: "**bold** word".)
    expect(markdown).not.toContain("** File upload");
    expect(markdown).not.toContain("** Trailing space");
  });

  it("renders BlockNote table blocks as markdown pipe-tables (treating row 0 as header)", () => {
    const cell = (text: string, bold = false) => [{ type: "text", text, styles: bold ? { bold: true } : {} }];
    const content = JSON.stringify([
      {
        type: "table",
        props: { textColor: "default", backgroundColor: "default" },
        content: {
          type: "tableContent",
          rows: [
            { cells: [cell("Action"), cell("Personal"), cell("Admin")] },
            { cells: [cell("Read"), cell("yes"), cell("yes")] },
            { cells: [cell("Delete the | workspace"), cell("no"), cell("yes")] },
          ],
        },
        children: [],
      },
    ]);

    const markdown = blockNoteJsonToMarkdown(content);

    // Header row + separator + body rows
    expect(markdown).toContain("| Action | Personal | Admin |");
    expect(markdown).toContain("| --- | --- | --- |");
    expect(markdown).toContain("| Read | yes | yes |");
    // Pipes inside cell content must be escaped so they don't break the table syntax
    expect(markdown).toContain("Delete the \\| workspace");
  });

  it("leaves all-whitespace styled runs as plain text (no empty emphasis markers)", () => {
    const content = JSON.stringify([
      {
        type: "paragraph",
        content: [
          { type: "text", text: "before", styles: {} },
          { type: "text", text: "   ", styles: { bold: true } },
          { type: "text", text: "after", styles: {} },
        ],
        children: [],
      },
    ]);

    const markdown = blockNoteJsonToMarkdown(content);

    expect(markdown).toBe("before   after");
    expect(markdown).not.toContain("**");
  });
});
