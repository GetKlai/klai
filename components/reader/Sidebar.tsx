"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavNode } from "@/lib/gitea";

type Props = {
  tree: NavNode[];
  orgSlug: string;
  kbSlug: string;
  kbName: string;
};

export function Sidebar({ tree, orgSlug, kbSlug, kbName }: Props) {
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 border-r border-gray-100 bg-gray-50 min-h-screen px-4 py-6">
      <Link
        href={`/${kbSlug}`}
        className="block text-sm font-semibold text-gray-800 mb-4 hover:text-blue-600"
      >
        {kbName}
      </Link>
      <NavList nodes={tree} kbSlug={kbSlug} pathname={pathname} depth={0} />
    </aside>
  );
}

function NavList({
  nodes,
  kbSlug,
  pathname,
  depth,
}: {
  nodes: NavNode[];
  kbSlug: string;
  pathname: string;
  depth: number;
}) {
  return (
    <ul className={depth > 0 ? "ml-3 mt-1" : ""}>
      {nodes.map((node) => (
        <NavItem
          key={node.path}
          node={node}
          kbSlug={kbSlug}
          pathname={pathname}
          depth={depth}
        />
      ))}
    </ul>
  );
}

function NavItem({
  node,
  kbSlug,
  pathname,
  depth,
}: {
  node: NavNode;
  kbSlug: string;
  pathname: string;
  depth: number;
}) {
  const articlePath = node.path.replace(/\.md$/, "");
  const href = `/${kbSlug}/${articlePath}`;
  const isActive = pathname === href;

  if (node.type === "dir") {
    return (
      <li>
        <span className="block text-xs font-semibold uppercase tracking-wide text-gray-400 mt-4 mb-1">
          {node.title}
        </span>
        {node.children && (
          <NavList
            nodes={node.children}
            kbSlug={kbSlug}
            pathname={pathname}
            depth={depth + 1}
          />
        )}
      </li>
    );
  }

  return (
    <li>
      <Link
        href={href}
        className={`block text-sm py-1 rounded px-2 -mx-2 transition-colors ${
          isActive
            ? "bg-blue-50 text-blue-700 font-medium"
            : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
        }`}
      >
        {node.title}
      </Link>
    </li>
  );
}
