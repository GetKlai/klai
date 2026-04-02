import { NextRequest, NextResponse } from "next/server";
import { requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { parsePage, parseSidebar, serializeSidebar, serializePage, slugify } from "@/lib/markdown";

type Params = { org: string; kb: string };

// POST /api/orgs/{org}/kbs/{kb}/upload
// Multipart: file (.md), folder (target folder path, optional)
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  try {
    const { org: orgSlug, kb: kbSlug } = await params;
    const access = await requireOrgAccess(request, orgSlug);
    if (access.error) return access.error;
    const { payload, org } = access;

    const kb = await db.getKB(org.id, kbSlug);
    if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });
    const denied = checkKBAccess(kb, payload.sub);
    if (denied) return denied;

    const formData = await request.formData();
    const file = formData.get("file") as File | null;
    const targetFolder = (formData.get("folder") as string | null) ?? "";

    if (!file || !file.name.endsWith(".md")) {
      return NextResponse.json({ error: "Only .md files are accepted" }, { status: 400 });
    }

    const raw = await file.text();
    const parsed = parsePage(raw);

    // Derive title from frontmatter if set, otherwise from the filename.
    // Inject the title into frontmatter if it was missing so the editor
    // shows the correct page title without triggering an auto-rename on
    // first open (doSave compares slugify(title) against the file slug).
    const title = parsed.frontmatter.title ?? file.name.replace(/\.md$/, "");
    let fileContent = raw;
    if (!parsed.frontmatter.title) {
      fileContent = serializePage({ ...parsed.frontmatter, title }, parsed.content);
    }

    // Normalize filename to a URL-safe slug so it matches slugify(title).
    const slug = slugify(title);
    const normalizedFileName = `${slug}.md`;
    const filePath = targetFolder ? `${targetFolder}/${normalizedFileName}` : normalizedFileName;

    // Write file to Gitea (upsert: GET sha first so we can overwrite existing files)
    const existingFile = await gitea.getFile(kb.gitea_repo, filePath);
    await gitea.putFile(
      kb.gitea_repo,
      filePath,
      fileContent,
      `Upload ${filePath}`,
      existingFile?.sha
    );

    // Add slug to _sidebar.yaml (primary nav source)
    const sidebarFile = await gitea.getFile(kb.gitea_repo, "_sidebar.yaml");
    const manifest = sidebarFile
      ? parseSidebar(Buffer.from(sidebarFile.content ?? "", "base64").toString("utf-8"))
      : { pages: [] };

    const entrySlug = targetFolder ? `${targetFolder}/${slug}` : slug;
    if (!manifest.pages.some((p) => p.slug === entrySlug)) {
      manifest.pages.push({ slug: entrySlug });
    }

    await gitea.putFile(
      kb.gitea_repo,
      "_sidebar.yaml",
      serializeSidebar(manifest),
      `Add ${entrySlug} to sidebar`,
      sidebarFile?.sha
    );

    return NextResponse.json({
      path: filePath,
      title,
    });
  } catch (err) {
    console.error("[upload] error:", err instanceof Error ? err.stack : err);
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
