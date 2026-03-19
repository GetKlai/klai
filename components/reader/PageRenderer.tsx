import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";
import rehypeHighlight from "rehype-highlight";

type Props = {
  content: string;
};

export function PageRenderer({ content }: Props) {
  return (
    <article className="prose prose-gray max-w-none prose-a:text-blue-600 prose-a:underline">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSlug, rehypeHighlight]}
      >
        {content}
      </ReactMarkdown>
    </article>
  );
}
