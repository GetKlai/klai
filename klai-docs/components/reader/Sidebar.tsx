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

export function Sidebar({ tree, kbSlug, kbName }: Props) {
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 border-r border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] min-h-screen px-5 py-8">
      <Link
        href={`/${kbSlug}`}
        className="block font-[family-name:var(--font-display-medium)] text-[15px] text-rl-dark mb-8 hover:text-[var(--color-rl-accent-dark)] transition-colors"
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
        <span className="block text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.06em] text-[var(--color-rl-muted)] mt-5 mb-2">
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
        className={`block text-[14px] py-1.5 rounded-md px-2 -mx-2 transition-colors ${
          isActive
            ? "bg-[var(--color-rl-accent)]/15 text-rl-dark font-[family-name:var(--font-display-medium)]"
            : "text-[var(--color-rl-dark-60)] hover:text-rl-dark hover:bg-[var(--color-rl-dark-10)]"
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
