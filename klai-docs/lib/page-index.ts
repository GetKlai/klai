/**
 * Shared page-index builder.
 * Used by GET /page-index (read-only) and PUT /pages (after creation, for combined response).
 */
import * as gitea from "@/lib/gitea";
import { parseSidebar, parsePage, type SidebarEntry } from "@/lib/markdown";

export type PageIndexEntry = {
  id: string | null;
  slug: string;
  title: string;
  icon?: string;
};

/** Recursively collect all slugs from a sidebar entry tree. */
function collectSlugs(entries: SidebarEntry[]): string[] {
  const slugs: string[] = [];
  for (const entry of entries) {
    slugs.push(entry.slug);
    if (entry.children?.length) {
      slugs.push(...collectSlugs(entry.children));
    }
  }
  return slugs;
}

/**
 * Build the full page index for a KB from its Gitea repo.
 * Returns an empty array when _sidebar.yaml does not exist.
 */
export async function buildPageIndex(giteaRepo: string): Promise<PageIndexEntry[]> {
  const sidebarRaw = await gitea.getFileContent(giteaRepo, "_sidebar.yaml");
  if (!sidebarRaw) return [];

  const manifest = parseSidebar(sidebarRaw);
  const slugs = collectSlugs(manifest.pages).slice(0, 100);

  const entries: PageIndexEntry[] = [];
  for (const slug of slugs) {
    const raw = await gitea.getFileContent(giteaRepo, `${slug}.md`);
    if (!raw) continue;
    const { frontmatter } = parsePage(raw);
    const defaultTitle = slug.split("/").at(-1)!.replace(/-/g, " ");
    entries.push({
      id: frontmatter.id ?? null,
      slug,
      title: frontmatter.title ?? defaultTitle,
      ...(frontmatter.icon ? { icon: frontmatter.icon } : {}),
    });
  }
  return entries;
}
