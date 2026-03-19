"use client";

import { useCallback, useState } from "react";
import { useCreateBlockNote } from "@blocknote/react";
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { useRouter } from "next/navigation";
import { slugify } from "@/lib/markdown";

type Props = {
  orgSlug: string;
  kbSlug: string;
  filePath: string | null;
  sha?: string;
  initialTitle: string;
  initialContent: string;
  isNew: boolean;
};

export function BlockEditor({
  orgSlug,
  kbSlug,
  filePath,
  sha,
  initialTitle,
  initialContent,
  isNew,
}: Props) {
  const router = useRouter();
  const [title, setTitle] = useState(initialTitle);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const editor = useCreateBlockNote({
    initialContent: initialContent
      ? undefined // BlockNote doesn't take raw markdown — convert below
      : undefined,
  });

  // Convert initialContent markdown → BlockNote blocks on mount
  // BlockNote v0.30 provides markdownToBlocks via the editor instance
  const loadInitialContent = useCallback(async () => {
    if (!initialContent) return;
    const blocks = await editor.tryParseMarkdownToBlocks(initialContent);
    editor.replaceBlocks(editor.document, blocks);
  }, [editor, initialContent]);

  // Called once on mount
  useState(() => {
    loadInitialContent();
  });

  const handleSave = async () => {
    setSaving(true);
    setError("");

    try {
      // Serialize BlockNote blocks → markdown
      const markdown = await editor.blocksToMarkdownLossy(editor.document);

      const resolvedPath = filePath ?? `${slugify(title) || "untitled"}.md`;

      const res = await fetch(
        `/api/orgs/${orgSlug}/kbs/${kbSlug}/pages/${resolvedPath.replace(/\.md$/, "")}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title,
            content: markdown,
            sha,
          }),
        }
      );

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? "Save failed");
      }

      router.push(`/admin/${kbSlug}`);
      router.refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-100 bg-white">
        <a
          href={`/admin/${kbSlug}`}
          className="text-sm text-gray-400 hover:text-gray-600"
        >
          ← Back
        </a>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Page title"
          className="flex-1 text-lg font-medium bg-transparent border-none outline-none placeholder:text-gray-300"
        />
        <div className="flex items-center gap-2">
          {error && <span className="text-sm text-red-500">{error}</span>}
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 overflow-y-auto px-6 py-8 max-w-3xl mx-auto w-full">
        <BlockNoteView editor={editor} theme="light" />
      </div>
    </div>
  );
}
