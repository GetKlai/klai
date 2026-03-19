"use client";

import Link from "next/link";
import type { NavNode } from "@/lib/gitea";

type Props = {
  tree: NavNode[];
  orgSlug: string;
  kbSlug: string;
  giteaRepo: string;
};

export function NavTreeEditor({ tree, kbSlug }: Props) {
  if (tree.length === 0) {
    return (
      <div className="text-center py-16 text-gray-400">
        <p>No pages yet.</p>
        <Link
          href={`/admin/${kbSlug}/edit/`}
          className="mt-3 inline-block text-sm text-blue-600 hover:underline"
        >
          Create your first page
        </Link>
      </div>
    );
  }

  return (
    <ul className="border border-gray-100 rounded divide-y divide-gray-100">
      {tree.map((node) => (
        <TreeRow key={node.path} node={node} kbSlug={kbSlug} depth={0} />
      ))}
    </ul>
  );
}

function TreeRow({
  node,
  kbSlug,
  depth,
}: {
  node: NavNode;
  kbSlug: string;
  depth: number;
}) {
  const articlePath = node.path.replace(/\.md$/, "");

  if (node.type === "dir") {
    return (
      <>
        <li
          className="flex items-center gap-2 px-4 py-2 bg-gray-50"
          style={{ paddingLeft: `${16 + depth * 16}px` }}
        >
          <span className="text-gray-400">📁</span>
          <span className="text-sm font-medium text-gray-600">{node.title}</span>
        </li>
        {node.children?.map((child) => (
          <TreeRow key={child.path} node={child} kbSlug={kbSlug} depth={depth + 1} />
        ))}
      </>
    );
  }

  return (
    <li
      className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50"
      style={{ paddingLeft: `${16 + depth * 16}px` }}
    >
      <span className="text-sm text-gray-700">{node.title}</span>
      <div className="flex gap-2">
        <Link
          href={`/${kbSlug}/${articlePath}`}
          target="_blank"
          className="text-xs text-gray-400 hover:text-gray-600"
        >
          View
        </Link>
        <Link
          href={`/admin/${kbSlug}/edit/${articlePath}`}
          className="text-xs text-blue-600 hover:text-blue-800"
        >
          Edit
        </Link>
      </div>
    </li>
  );
}
