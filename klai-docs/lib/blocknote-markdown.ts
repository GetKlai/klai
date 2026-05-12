export type PageIndexEntry = {
  id: string;
  slug: string;
  title?: string;
};

type InlineText = {
  type: "text";
  text: string;
  styles?: Record<string, boolean | string>;
};

type InlineLink = {
  type: "link";
  href?: string;
  content?: InlineContent[];
};

type InlineWikiLink = {
  type: "wikilink";
  props?: {
    pageId?: string;
    title?: string;
    icon?: string;
  };
};

type InlineContent = string | InlineText | InlineLink | InlineWikiLink;

type BlockNoteBlock = {
  type?: string;
  props?: Record<string, unknown>;
  content?: InlineContent[] | string;
  children?: BlockNoteBlock[];
};

const LIST_TYPES = new Set(["bulletListItem", "numberedListItem", "checkListItem"]);

export function blockNoteJsonToMarkdown(
  content: string,
  pageIndex: PageIndexEntry[] = [],
  kbSlug = ""
): string | null {
  const trimmed = content.trimStart();
  if (!trimmed.startsWith("[")) return null;

  let blocks: unknown;
  try {
    blocks = JSON.parse(content);
  } catch {
    return null;
  }

  if (!Array.isArray(blocks) || !blocks.every(isBlockNoteBlock)) return null;
  return renderBlocks(blocks, pageIndex, kbSlug).trim();
}

function isBlockNoteBlock(value: unknown): value is BlockNoteBlock {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { type?: unknown; content?: unknown; children?: unknown };
  if (candidate.type !== undefined && typeof candidate.type !== "string") return false;
  if (candidate.children !== undefined && !Array.isArray(candidate.children)) return false;
  return true;
}

function renderBlocks(
  blocks: BlockNoteBlock[],
  pageIndex: PageIndexEntry[],
  kbSlug: string,
  depth = 0
): string {
  const rendered: string[] = [];

  for (let i = 0; i < blocks.length; i += 1) {
    const block = blocks[i];
    if (LIST_TYPES.has(block.type ?? "")) {
      const list: string[] = [];
      let number = 1;
      while (i < blocks.length && blocks[i].type === block.type) {
        list.push(renderListItem(blocks[i], pageIndex, kbSlug, depth, number));
        number += 1;
        i += 1;
      }
      i -= 1;
      rendered.push(list.join("\n"));
      continue;
    }

    const markdown = renderBlock(block, pageIndex, kbSlug, depth);
    if (markdown) rendered.push(markdown);
  }

  return rendered.join("\n\n");
}

function renderBlock(
  block: BlockNoteBlock,
  pageIndex: PageIndexEntry[],
  kbSlug: string,
  depth: number
): string {
  const text = renderInlineContent(block.content, pageIndex, kbSlug);
  const children = block.children?.length
    ? renderBlocks(block.children, pageIndex, kbSlug, depth + 1)
    : "";

  switch (block.type) {
    case "heading": {
      const level = clampHeadingLevel(block.props?.level);
      return joinBlockParts(`${"#".repeat(level)} ${text}`.trim(), children);
    }
    case "codeBlock": {
      const language = typeof block.props?.language === "string" ? block.props.language : "";
      return joinBlockParts(`\`\`\`${language}\n${extractPlainText(block.content)}\n\`\`\``, children);
    }
    case "quote": {
      const quote = text
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n");
      return joinBlockParts(quote, children);
    }
    case "image": {
      const url = typeof block.props?.url === "string" ? block.props.url : "";
      const caption = text || (typeof block.props?.caption === "string" ? block.props.caption : "");
      return isSafeHref(url) ? joinBlockParts(`![${escapeMarkdown(caption)}](${escapeHref(url)})`, children) : children;
    }
    case "file": {
      const url = typeof block.props?.url === "string" ? block.props.url : "";
      const name = text || (typeof block.props?.name === "string" ? block.props.name : "Download");
      return isSafeHref(url) ? joinBlockParts(`[${escapeMarkdown(name)}](${escapeHref(url)})`, children) : children;
    }
    case "paragraph":
    default:
      return joinBlockParts(text, children);
  }
}

function renderListItem(
  block: BlockNoteBlock,
  pageIndex: PageIndexEntry[],
  kbSlug: string,
  depth: number,
  number: number
): string {
  const indent = "  ".repeat(depth);
  const text = renderInlineContent(block.content, pageIndex, kbSlug);
  const marker =
    block.type === "numberedListItem"
      ? `${number}.`
      : block.type === "checkListItem"
        ? `- [${block.props?.checked ? "x" : " "}]`
        : "-";
  const firstLine = `${indent}${marker} ${text}`.trimEnd();
  const children = block.children?.length
    ? `\n${renderBlocks(block.children, pageIndex, kbSlug, depth + 1)}`
    : "";
  return `${firstLine}${children}`;
}

function renderInlineContent(
  content: InlineContent[] | string | undefined,
  pageIndex: PageIndexEntry[],
  kbSlug: string
): string {
  if (!content) return "";
  if (typeof content === "string") return escapeMarkdown(content);
  if (!Array.isArray(content)) return "";
  return content.map((item) => renderInline(item, pageIndex, kbSlug)).join("");
}

function renderInline(
  item: InlineContent,
  pageIndex: PageIndexEntry[],
  kbSlug: string
): string {
  if (typeof item === "string") return escapeMarkdown(item);

  if (item.type === "text") {
    return applyStyles(escapeMarkdown(item.text), item.styles ?? {});
  }

  if (item.type === "link") {
    const label = renderInlineContent(item.content, pageIndex, kbSlug);
    if (!item.href || !isSafeHref(item.href)) return label;
    return `[${label}](${escapeHref(item.href)})`;
  }

  if (item.type === "wikilink") {
    const pageId = item.props?.pageId;
    const page = pageId ? pageIndex.find((entry) => entry.id === pageId || entry.slug === pageId) : undefined;
    const title = item.props?.title ?? page?.title ?? page?.slug ?? "Untitled";
    if (!page || !kbSlug) return escapeMarkdown(title);
    return `[${escapeMarkdown(title)}](/docs/${kbSlug}/${page.slug})`;
  }

  return "";
}

function applyStyles(text: string, styles: Record<string, boolean | string>): string {
  if (!text) return text;

  const hasStyle =
    styles.code || styles.bold || styles.italic || styles.strike || styles.underline;
  if (!hasStyle) return text;

  // Markdown emphasis markers cannot have whitespace between the marker and the
  // wrapped content: "** foo **" renders as literal asterisks, "**foo**" renders
  // bold. Pull leading/trailing whitespace out of the styled span so the run
  // still renders with emphasis even when authors include surrounding spaces in
  // the BlockNote text-run (a common shape produced by the editor itself).
  const match = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
  if (!match || !match[2]) return text;
  const [, leading, core, trailing] = match;

  let value = core;
  if (styles.code) value = `\`${value.replace(/`/g, "\\`")}\``;
  if (styles.bold) value = `**${value}**`;
  if (styles.italic) value = `_${value}_`;
  if (styles.strike) value = `~~${value}~~`;
  if (styles.underline) value = `<u>${value}</u>`;
  return `${leading}${value}${trailing}`;
}

function extractPlainText(content: InlineContent[] | string | undefined): string {
  if (!content) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((item) => {
      if (typeof item === "string") return item;
      if (item.type === "text") return item.text;
      if (item.type === "link") return extractPlainText(item.content);
      if (item.type === "wikilink") return item.props?.title ?? "";
      return "";
    })
    .join("");
}

function clampHeadingLevel(level: unknown): number {
  if (typeof level !== "number" || !Number.isFinite(level)) return 2;
  return Math.min(6, Math.max(1, Math.trunc(level)));
}

function joinBlockParts(primary: string, children: string): string {
  if (primary && children) return `${primary}\n\n${children}`;
  return primary || children;
}

function isSafeHref(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("/") || trimmed.startsWith("#")) return true;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) return true;
  try {
    const url = new URL(trimmed);
    return ["http:", "https:", "mailto:", "tel:"].includes(url.protocol);
  } catch {
    return false;
  }
}

function escapeHref(href: string): string {
  return href.trim().replace(/\)/g, "%29").replace(/\s/g, "%20");
}

function escapeMarkdown(text: string): string {
  return text.replace(/([\\`*_{}\[\]#+\-.!|>])/g, "\\$1");
}
