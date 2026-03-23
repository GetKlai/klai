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
      icon: { default: "" },
    },
    content: "none" as const,
  },
  {
    render: ({ inlineContent }) => (
      <a
        data-wikilink-page-id={inlineContent.props.pageId}
        style={{
          color: "var(--color-purple-accent)",
          textDecoration: "none",
          cursor: "pointer",
          borderBottom: "1px solid var(--color-purple-accent)",
          display: "inline-flex",
          alignItems: "center",
          gap: "2px",
        }}
        title={`Ga naar: ${inlineContent.props.title}`}
      >
        {inlineContent.props.icon && (
          <span style={{ marginRight: "3px" }}>{inlineContent.props.icon}</span>
        )}
        {inlineContent.props.title}
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ display: "inline", flexShrink: 0, marginLeft: "1px", opacity: 0.7 }}
        >
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
          <polyline points="15 3 21 3 21 9" />
          <line x1="10" y1="14" x2="21" y2="3" />
        </svg>
      </a>
    ),
    toExternalHTML: ({ inlineContent }) => (
      <a
        data-wikilink={inlineContent.props.pageId}
        data-title={inlineContent.props.title}
        data-icon={inlineContent.props.icon}
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
        icon: element.getAttribute("data-icon") ?? "",
      }
    },
  }
)
