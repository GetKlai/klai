import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";

export type PageIndexEntry = {
  id: string;
  slug: string;
  title?: string;
};

type Props = {
  content: string;
  pageIndex?: PageIndexEntry[];
  kbSlug?: string;
};

/**
 * Resolves data-wikilink UUID attributes in raw markdown content to proper
 * href attributes before passing to ReactMarkdown. This runs as a string
 * replacement so the resulting <a href="..."> tags are rendered by rehype-raw.
 *
 * Input:  <a data-wikilink="uuid" data-title="Title">Title</a>
 * Output: <a href="/docs/{kbSlug}/{slug}" data-wikilink="uuid" data-title="Title">Title</a>
 */
function resolveWikilinks(
  content: string,
  pageIndex: PageIndexEntry[],
  kbSlug: string
): string {
  return content.replace(
    /<a\s+data-wikilink="([^"]+)"([^>]*)>/g,
    (match, uuid, rest) => {
      const page = pageIndex.find((p) => p.id === uuid);
      if (!page) return match;
      return `<a href="/docs/${kbSlug}/${page.slug}" data-wikilink="${uuid}"${rest}>`;
    }
  );
}

export function PageRenderer({ content, pageIndex, kbSlug }: Props) {
  const resolvedContent =
    pageIndex && pageIndex.length > 0 && kbSlug
      ? resolveWikilinks(content, pageIndex, kbSlug)
      : content;

  return (
    <article className="prose prose-gray max-w-none prose-a:text-blue-600 prose-a:underline">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSlug, rehypeHighlight]}
      >
        {resolvedContent}
      </ReactMarkdown>
    </article>
  );
}
