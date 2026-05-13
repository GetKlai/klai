/**
 * Payload-driven XSS sanitization tests for rehype-sanitize.
 *
 * PR #313 added rehype-sanitize to PageRenderer to close stored-XSS via
 * tenant-authored markdown but shipped without tests. These tests exercise
 * the SAME sanitizeSchema (from lib/sanitize-schema.ts) that the renderer
 * uses, in isolation from React.
 *
 * Test groups:
 *   A) script + event-handler payloads — assert stripped
 *   B) wikilink data-* attrs — assert preserved
 *   C) legitimate markdown HTML — assert preserved
 *   D) obfuscation attempts — assert stripped
 */

import { describe, it, expect } from "vitest";
import { unified } from "unified";
import rehypeParse from "rehype-parse";
import rehypeSanitize from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";
import { sanitizeSchema } from "../lib/sanitize-schema";

/**
 * Run the sanitizer pipeline on a raw HTML fragment and return the sanitized
 * HTML string. Uses the SAME sanitizeSchema that PageRenderer uses.
 */
async function runSanitize(html: string): Promise<string> {
  const result = await unified()
    .use(rehypeParse, { fragment: true })
    .use(rehypeSanitize, sanitizeSchema)
    .use(rehypeStringify)
    .process(html);
  return String(result);
}

// ---------------------------------------------------------------------------
// GROUP A — script + event handlers (MUST be stripped)
// ---------------------------------------------------------------------------
describe("Group A: script tags and event-handler attributes must be stripped", () => {
  it("strips <script> tags", async () => {
    const out = await runSanitize("<script>alert(1)</script>");
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toContain("alert(1)");
  });

  it("strips onerror attribute from <img>", async () => {
    const out = await runSanitize('<img src=x onerror="alert(1)">');
    expect(out).not.toMatch(/onerror/i);
  });

  it("strips onload attribute from <svg>", async () => {
    const out = await runSanitize('<svg onload="alert(1)"></svg>');
    expect(out).not.toMatch(/onload/i);
  });

  it("strips javascript: href from <a>", async () => {
    const out = await runSanitize('<a href="javascript:alert(1)">click</a>');
    expect(out).not.toMatch(/javascript:/i);
  });

  it("strips vbscript: href from <a>", async () => {
    const out = await runSanitize('<a href="vbscript:alert(1)">click</a>');
    expect(out).not.toMatch(/vbscript:/i);
  });

  it("strips <iframe> with javascript: src", async () => {
    const out = await runSanitize('<iframe src="javascript:alert(1)"></iframe>');
    expect(out).not.toMatch(/<iframe/i);
    expect(out).not.toMatch(/javascript:/i);
  });

  it("strips onload attribute from <body>", async () => {
    const out = await runSanitize('<body onload="alert(1)">');
    expect(out).not.toMatch(/onload/i);
  });

  it("strips data:text/html href from <a>", async () => {
    const out = await runSanitize(
      '<a href="data:text/html,<script>alert(1)</script>">click</a>'
    );
    expect(out).not.toMatch(/data:text\/html/i);
    expect(out).not.toMatch(/<script/i);
  });
});

// ---------------------------------------------------------------------------
// GROUP B — wikilink data-* attrs (MUST survive)
// ---------------------------------------------------------------------------
describe("Group B: wikilink data-* attributes must be preserved", () => {
  it("preserves data-wikilink and data-title on <a> with href", async () => {
    const out = await runSanitize(
      '<a href="/docs/foo/bar" data-wikilink="abc-123" data-title="Foo">Foo</a>'
    );
    expect(out).toContain('data-wikilink="abc-123"');
    expect(out).toContain('data-title="Foo"');
    expect(out).toContain('href="/docs/foo/bar"');
  });

  it("preserves data-wikilink and data-title on <a> without href", async () => {
    const out = await runSanitize(
      '<a data-wikilink="uuid" data-title="X">link</a>'
    );
    expect(out).toContain('data-wikilink="uuid"');
    expect(out).toContain('data-title="X"');
  });
});

// ---------------------------------------------------------------------------
// GROUP C — legitimate markdown HTML (MUST survive)
// ---------------------------------------------------------------------------
describe("Group C: legitimate markdown HTML must survive sanitization", () => {
  it("preserves <p> with <strong>", async () => {
    const out = await runSanitize("<p>Hello <strong>world</strong></p>");
    expect(out).toContain("<p>");
    expect(out).toContain("<strong>");
    expect(out).toContain("Hello");
  });

  it("preserves <ul><li>", async () => {
    const out = await runSanitize("<ul><li>item</li></ul>");
    expect(out).toContain("<ul>");
    expect(out).toContain("<li>");
    expect(out).toContain("item");
  });

  it("preserves <h1> with id attribute", async () => {
    const out = await runSanitize('<h1 id="my-anchor">Title</h1>');
    expect(out).toContain("<h1");
    expect(out).toContain('id="user-content-my-anchor"');
    expect(out).toContain("Title");
  });

  it("preserves inline <code>", async () => {
    const out = await runSanitize("<code>foo</code>");
    expect(out).toContain("<code>");
    expect(out).toContain("foo");
  });

  it("preserves <pre><code> block", async () => {
    const out = await runSanitize("<pre><code>foo</code></pre>");
    expect(out).toContain("<pre>");
    expect(out).toContain("<code>");
    expect(out).toContain("foo");
  });

  it("preserves <table><tr><td>", async () => {
    const out = await runSanitize(
      "<table><tr><td>cell</td></tr></table>"
    );
    expect(out).toContain("<table>");
    expect(out).toContain("<td>");
    expect(out).toContain("cell");
  });

  it("preserves <img> with src and alt", async () => {
    const out = await runSanitize('<img src="/path/to/img.png" alt="x">');
    expect(out).toContain("<img");
    expect(out).toContain('src="/path/to/img.png"');
    expect(out).toContain('alt="x"');
  });
});

// ---------------------------------------------------------------------------
// GROUP D — obfuscation attempts (MUST be stripped)
// ---------------------------------------------------------------------------
describe("Group D: obfuscation and evasion attempts must be stripped", () => {
  it("strips mixed-case <ScRiPt>", async () => {
    const out = await runSanitize("<ScRiPt>alert(1)</ScRiPt>");
    expect(out).not.toMatch(/<script/i);
  });

  it("strips onerror with whitespace padding", async () => {
    const out = await runSanitize('<img src=x  onerror = "alert(1)">');
    expect(out).not.toMatch(/onerror/i);
  });

  it("strips HTML-entity-obfuscated javascript: href", async () => {
    const out = await runSanitize(
      '<a href="java&#115;cript:alert(1)">x</a>'
    );
    // After HTML parsing, &#115; becomes 's' → "javascript:" — must be stripped
    expect(out).not.toMatch(/javascript:/i);
  });

  it("strips SVG polyglot with xlink:href data: script", async () => {
    const out = await runSanitize(
      '<svg><script xlink:href="data:,alert(1)" /></svg>'
    );
    expect(out).not.toMatch(/<script/i);
    // data: on xlink:href must not survive
    expect(out).not.toMatch(/data:,alert/);
  });

  it("strips or neutralizes style with javascript: CSS expression", async () => {
    const out = await runSanitize(
      '<div style="background:url(javascript:alert(1))">x</div>'
    );
    // Either the style attr is stripped entirely, or the javascript: value is gone
    const hasJsInStyle =
      out.includes("javascript:") && out.includes("style=");
    expect(hasJsInStyle).toBe(false);
  });
});
