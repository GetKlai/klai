import matter from "gray-matter";
import yaml from "js-yaml";

export type PageFrontmatter = {
  title?: string;
  description?: string;
  edit_access?: "org" | string[];
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

/**
 * Returns a slug-safe string from a title.
 */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}
