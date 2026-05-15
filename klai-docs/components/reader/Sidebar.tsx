"use client";

import { useEffect, useState } from "react";
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
  const [open, setOpen] = useState(false);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <>
      {/* Mobile top bar with hamburger — hidden on md+ */}
      <div className="md:hidden sticky top-0 z-30 flex items-center gap-3 border-b border-[var(--color-rl-border)] bg-[var(--color-rl-bg)] px-4 py-3">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md text-rl-dark hover:bg-[var(--color-rl-dark-10)] transition-colors"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <Link
          href={`/${kbSlug}`}
          className="font-[family-name:var(--font-display-medium)] text-[15px] text-rl-dark truncate"
        >
          {kbName}
        </Link>
      </div>

      {/* Backdrop for mobile drawer */}
      {open && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-[var(--color-rl-dark)]/30"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`
          fixed inset-y-0 left-0 z-50 w-72 transform overflow-y-auto transition-transform duration-200 ease-out
          ${open ? "translate-x-0" : "-translate-x-full"}
          md:static md:z-auto md:w-64 md:translate-x-0 md:transform-none
          shrink-0 border-r border-[var(--color-rl-border)] bg-[var(--color-rl-cream)] min-h-screen px-5 py-8
        `}
      >
        <div className="flex items-center justify-between mb-8">
          <Link
            href={`/${kbSlug}`}
            className="block font-[family-name:var(--font-display-medium)] text-[15px] text-rl-dark hover:text-[var(--color-rl-accent-dark)] transition-colors truncate"
          >
            {kbName}
          </Link>
          {/* Close button — mobile only */}
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close menu"
            className="md:hidden inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-rl-dark-60)] hover:bg-[var(--color-rl-dark-10)] hover:text-rl-dark transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <NavList nodes={tree} kbSlug={kbSlug} pathname={pathname} depth={0} />
      </aside>
    </>
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
        <span className="flex items-center gap-1.5 text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.06em] text-[var(--color-rl-muted)] mt-5 mb-2">
          {node.icon && <span className="text-[13px] not-italic">{node.icon}</span>}
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
        className={`flex items-center gap-2 text-[14px] py-1.5 rounded-md px-2 -mx-2 transition-colors ${
          isActive
            ? "bg-[var(--color-rl-accent)]/15 text-rl-dark font-[family-name:var(--font-display-medium)]"
            : "text-[var(--color-rl-dark-60)] hover:text-rl-dark hover:bg-[var(--color-rl-dark-10)]"
        }`}
      >
        {node.icon && <span className="shrink-0 text-[15px] leading-none">{node.icon}</span>}
        <span className="truncate">{node.title}</span>
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
