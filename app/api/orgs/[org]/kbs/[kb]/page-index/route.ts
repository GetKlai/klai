import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { parseSidebar, parsePage, type SidebarEntry } from "@/lib/markdown";

type Params = { org: string; kb: string };

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

export type PageIndexEntry = {
  id: string | null;
  slug: string;
  title: string;
};

// GET /api/orgs/{org}/kbs/{kb}/page-index
// Returns all pages with their stable id, slug, and title.
// Pages without an id have id: null; the real UUID is assigned on the next save.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const payload = await requireAuth(request);
  if (!payload?.sub)
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug } = await params;

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const sidebarRaw = await gitea.getFileContent(kb.gitea_repo, "_sidebar.yaml");
  if (!sidebarRaw) return NextResponse.json([]);

  const manifest = parseSidebar(sidebarRaw);
  const slugs = collectSlugs(manifest.pages).slice(0, 100);

  const entries: PageIndexEntry[] = [];

  for (const slug of slugs) {
    const raw = await gitea.getFileContent(kb.gitea_repo, `${slug}.md`);
    if (!raw) continue;

    const { frontmatter } = parsePage(raw);

    const defaultTitle = slug.split("/").at(-1)!.replace(/-/g, " ");
    entries.push({
      id: frontmatter.id ?? null,
      slug,
      title: frontmatter.title ?? defaultTitle,
    });
  }

  return NextResponse.json(entries);
}
