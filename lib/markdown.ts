import matter from "gray-matter";
import yaml from "js-yaml";

export type PageFrontmatter = {
  title?: string;
  description?: string;
  icon?: string;
  edit_access?: "org" | string[];
  redirects?: string[];
};

export type ParsedPage = {
  frontmatter: PageFrontmatter;
  content: string;
};

export function parsePage(raw: string): ParsedPage {
  const { data, content } = matter(raw);
  return {
    frontmatter: data as PageFrontmatter,
    content,
  };
}

export function serializePage(frontmatter: PageFrontmatter, content: string): string {
  const fm = yaml.dump(frontmatter, { lineWidth: -1 }).trimEnd();
  return `---\n${fm}\n---\n\n${content}`;
}

export type FolderMeta = {
  title?: string;
  order?: string[];
  labels?: Record<string, string>;
};

export function parseMeta(raw: string): FolderMeta {
  return (yaml.load(raw) ?? {}) as FolderMeta;
}

export function serializeMeta(meta: FolderMeta): string {
  return yaml.dump(meta, { lineWidth: -1 });
}

// ─── Sidebar manifest ─────────────────────────────────────────────────────────

export type SidebarEntry = {
  slug: string;
  children?: SidebarEntry[];
};

export type SidebarManifest = {
  pages: SidebarEntry[];
};

export function parseSidebar(raw: string): SidebarManifest {
  const data = (yaml.load(raw) ?? {}) as { pages?: SidebarEntry[] };
  return { pages: data.pages ?? [] };
}

export function serializeSidebar(manifest: SidebarManifest): string {
  return yaml.dump(manifest, { lineWidth: -1 });
}

/**
 * Recursively removes all occurrences of `slug` from the sidebar manifest tree.
 */
export function removeSlugFromSidebar(
  manifest: SidebarManifest,
  slug: string
): SidebarManifest {
  function filterEntries(entries: SidebarEntry[]): SidebarEntry[] {
    return entries
      .filter((e) => e.slug !== slug)
      .map((e) => ({
        ...e,
        ...(e.children ? { children: filterEntries(e.children) } : {}),
      }));
  }
  return { pages: filterEntries(manifest.pages) };
}

// ─── Slug helpers ─────────────────────────────────────────────────────────────

/**
 * Returns a slug-safe string from a title.
 */
export function slugify(title: string): string {
  return (
    title
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "") // strip accents
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .replace(/-+/g, "-") || "untitled"
  );
}

/**
 * Renames a slug in the sidebar manifest tree in-place.
 * All occurrences of oldSlug are replaced with newSlug.
 */
export function renameSidebarSlug(
  manifest: SidebarManifest,
  oldSlug: string,
  newSlug: string
): SidebarManifest {
  function renameInEntries(entries: SidebarEntry[]): SidebarEntry[] {
    return entries.map((e) => ({
      slug: e.slug === oldSlug ? newSlug : e.slug,
      ...(e.children ? { children: renameInEntries(e.children) } : {}),
    }));
  }
  return { pages: renameInEntries(manifest.pages) };
}
