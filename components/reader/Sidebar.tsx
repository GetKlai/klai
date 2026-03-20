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
    <aside className="w-64 shrink-0 border-r border-[rgba(45,27,105,0.08)] bg-[var(--color-sand-light)] min-h-screen px-5 py-6">
      <Link
        href={`/${kbSlug}`}
        className="block text-sm font-semibold text-[var(--color-purple-deep)] mb-6 hover:text-[var(--color-purple-accent)] transition-colors"
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
  const isExpandable = !!(node.children && node.children.length > 0);

  if (node.type === "dir") {
    return (
      <li>
        <span className="block text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-[rgba(26,26,26,0.35)] mt-4 mb-1">
          {node.title}
        </span>
        {isExpandable && (
          <NavList
            nodes={node.children!}
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
            ? "bg-[rgba(124,106,255,0.07)] text-[var(--color-purple-accent)] font-medium"
            : "text-[rgba(26,26,26,0.6)] hover:text-[var(--color-purple-deep)] hover:bg-[rgba(45,27,105,0.04)]"
        }`}
      >
        {node.title}
      </Link>
      {isExpandable && (
        <NavList
          nodes={node.children!}
          kbSlug={kbSlug}
          pathname={pathname}
          depth={depth + 1}
        />
      )}
    </li>
  );
}
