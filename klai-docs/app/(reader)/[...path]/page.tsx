import { notFound, redirect } from "next/navigation";
import { headers } from "next/headers";
import { db } from "@/lib/db";
import { buildNavTree, type NavNode } from "@/lib/gitea";
import * as gitea from "@/lib/gitea";
import { parsePage, parseSidebar } from "@/lib/markdown";
import type { SidebarEntry } from "@/lib/markdown";
import { Sidebar } from "@/components/reader/Sidebar";
import { PageRenderer, type PageIndexEntry } from "@/components/reader/PageRenderer";

/**
 * Collects all slug strings from a sidebar manifest tree.
 */
function collectSlugs(entries: SidebarEntry[]): string[] {
  const result: string[] = [];
  for (const e of entries) {
    result.push(e.slug);
    if (e.children?.length) result.push(...collectSlugs(e.children));
  }
  return result;
}

function findFirstPageSlug(nodes: NavNode[]): string | null {
  for (const node of nodes) {
    if (node.type === "file") return node.slug;
    const childSlug = node.children?.length ? findFirstPageSlug(node.children) : null;
    if (childSlug) return childSlug;
  }
  return null;
}

/**
 * Looks up a redirect target for the given slug by scanning all pages'
 * frontmatter `redirects` arrays. Returns the new slug if found, else null.
 * Only runs when the sidebar has fewer than 50 pages.
 */
async function findRedirectTarget(
  repo: string,
  requestedSlug: string
): Promise<string | null> {
  const sidebarRaw = await gitea.getFileContent(repo, "_sidebar.yaml");
  if (!sidebarRaw) return null;

  const manifest = parseSidebar(sidebarRaw);
  const slugs = collectSlugs(manifest.pages);

  if (slugs.length >= 50) return null;

  const results = await Promise.all(
    slugs.map(async (slug) => {
      const raw = await gitea.getFileContent(repo, `${slug}.md`);
      if (!raw) return null;
      const { frontmatter } = parsePage(raw);
      if (Array.isArray(frontmatter.redirects) && frontmatter.redirects.includes(requestedSlug)) {
        return slug;
      }
      return null;
    })
  );

  return results.find((r) => r !== null) ?? null;
}

/**
 * Builds a lightweight page index (id + slug + title) for wikilink resolution.
 * Reads each page's frontmatter from Gitea. Capped at 100 pages.
 */
async function buildPageIndex(repo: string): Promise<PageIndexEntry[]> {
  const sidebarRaw = await gitea.getFileContent(repo, "_sidebar.yaml");
  if (!sidebarRaw) return [];

  const manifest = parseSidebar(sidebarRaw);
  const slugs = collectSlugs(manifest.pages).slice(0, 100);
  const entries: PageIndexEntry[] = [];

  for (const slug of slugs) {
    const raw = await gitea.getFileContent(repo, `${slug}.md`);
    if (!raw) continue;
    const { frontmatter } = parsePage(raw);
    if (!frontmatter.id) continue;
    entries.push({
      id: frontmatter.id,
      slug,
      title: frontmatter.title,
    });
  }

  return entries;
}

/**
 * Route: /{kb}/[...slug]  OR  /{kb}/
 *
 * path[0] = kb slug
 * path[1..] = article path segments
 */
export default async function ReaderPage({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  const [kbSlug, ...articleSegments] = path;

  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org");
  if (!orgSlug) notFound();

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) notFound();

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) notFound();

  // Personal and private KBs are never served via the public reader
  if (kb.kb_type === "personal" || kb.visibility === "private") notFound();

  // Build sidebar navigation tree and page index for wikilink resolution
  const [navTree, pageIndex] = await Promise.all([
    buildNavTree(kb.gitea_repo),
    buildPageIndex(kb.gitea_repo),
  ]);

  // Determine article file path
  const articlePath =
    articleSegments.length > 0
      ? `${articleSegments.join("/")}.md`
      : null;

  if (!articlePath) {
    const firstSlug = findFirstPageSlug(navTree);
    if (firstSlug) redirect(`/${kbSlug}/${firstSlug}`);
  }

  let content = "";
  let title = kb.name;
  let description = "";
  let icon = "";

  if (articlePath) {
    const raw = await gitea.getFileContent(kb.gitea_repo, articlePath);
    if (!raw) {
      // Check if any existing page declares this slug in its redirects array
      const requestedSlug = articleSegments.join("/");
      const newSlug = await findRedirectTarget(kb.gitea_repo, requestedSlug);
      if (newSlug) {
        redirect(`/${kbSlug}/${newSlug}`);
      }
      notFound();
    }

    const parsed = parsePage(raw);
    content = parsed.content;
    title = parsed.frontmatter.title ?? title;
    description = parsed.frontmatter.description ?? "";
    icon = parsed.frontmatter.icon ?? "";
  }

  return (
    <div className="flex flex-col md:flex-row min-h-screen bg-[var(--color-rl-bg)]">
      <Sidebar
        tree={navTree}
        orgSlug={orgSlug}
        kbSlug={kbSlug}
        kbName={kb.name}
      />
      <main className="flex-1 w-full px-5 sm:px-8 md:px-16 py-8 md:py-12">
        <div className="mx-auto max-w-[780px]">
          {articlePath ? (
            <>
              {icon && (
                <div className="text-[40px] md:text-[48px] leading-none mb-4">{icon}</div>
              )}
              <h1 className="text-[32px] sm:text-[40px] md:text-[48px] tracking-[-0.04em] leading-[110%] text-rl-dark mb-6">{title}</h1>
              {description && (
                <p className="text-[16px] text-[var(--color-rl-dark-60)] leading-[1.6] mb-10 max-w-[55ch]">{description}</p>
              )}
              <PageRenderer content={content} pageIndex={pageIndex} kbSlug={kbSlug} />
            </>
          ) : (
            <div>
              <h1 className="text-[32px] sm:text-[40px] md:text-[48px] tracking-[-0.04em] leading-[110%] text-rl-dark mb-6">{kb.name}</h1>
              <p className="text-[16px] text-[var(--color-rl-dark-60)] leading-[1.6]">Select a page from the sidebar.</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  const [kbSlug, ...articleSegments] = path;

  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org");
  if (!orgSlug) return {};

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return {};

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return {};

  if (kb.kb_type === "personal" || kb.visibility === "private") return {};

  if (articleSegments.length > 0) {
    const raw = await gitea.getFileContent(
      kb.gitea_repo,
      `${articleSegments.join("/")}.md`
    );
    if (raw) {
      const { frontmatter } = parsePage(raw);
      return {
        title: `${frontmatter.title ?? kbSlug} · ${kb.name}`,
        description: frontmatter.description,
      };
    }
  }

  return { title: kb.name };
}
