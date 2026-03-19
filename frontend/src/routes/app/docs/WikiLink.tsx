import { createReactInlineContentSpec } from "@blocknote/react"

/**
 * WikiLink: a stable internal page reference stored as a UUID.
 *
 * In the editor it renders as a styled link showing the page title.
 * When serialised to markdown via toExternalHTML it emits:
 *   <a data-wikilink="{pageId}" data-title="{title}">{title}</a>
 *
 * The docs-app reader resolves the UUID to the current slug at render time,
 * so wikilinks survive page renames.
 */
export const WikiLink = createReactInlineContentSpec(
  {
    type: "wikilink" as const,
    propSchema: {
      pageId: { default: "" },
      title: { default: "Untitled" },
      kbSlug: { default: "" },
    },
    content: "none" as const,
  },
  {
    render: ({ inlineContent }) => (
      <a
        style={{
          color: "var(--color-purple-accent)",
          textDecoration: "underline",
          cursor: "pointer",
        }}
        title={`Ga naar: ${inlineContent.props.title}`}
      >
        {inlineContent.props.title}
      </a>
    ),
    toExternalHTML: ({ inlineContent }) => (
      <a
        data-wikilink={inlineContent.props.pageId}
        data-title={inlineContent.props.title}
      >
        {inlineContent.props.title}
      </a>
    ),
    parse: (element: HTMLElement) => {
      const pageId = element.getAttribute("data-wikilink")
      if (!pageId) return undefined
      return {
        pageId,
        title: element.getAttribute("data-title") ?? element.textContent ?? "Untitled",
        kbSlug: "",
      }
    },
  }
)
