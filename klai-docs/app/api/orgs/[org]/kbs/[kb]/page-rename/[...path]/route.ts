import { NextRequest, NextResponse } from "next/server";
import { requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import {
  serializePage,
  parsePage,
  parseSidebar,
  serializeSidebar,
  renameSidebarSlug,
} from "@/lib/markdown";

type Params = { org: string; kb: string; path: string[] };

// POST /api/orgs/{org}/kbs/{kb}/pages/{...path}/rename
// Body: { newSlug: string, title: string, content: string, icon?: string }
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug, path } = await params;
  const access = await requireOrgAccess(request, orgSlug);
  if (access.error) return access.error;
  const { payload, org } = access;

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });
  const denied = checkKBAccess(kb, payload.sub);
  if (denied) return denied;

  const oldSlug = path.join("/");
  const oldFilePath = `${oldSlug}.md`;

  const { newSlug, title, content, icon } = await request.json();

  if (!newSlug || typeof newSlug !== "string")
    return NextResponse.json({ error: "newSlug is required" }, { status: 400 });

  if (newSlug === oldSlug)
    return NextResponse.json({ error: "Slugs are identical" }, { status: 400 });

  // Read current file to get sha and existing frontmatter (for redirects)
  const oldFile = await gitea.getFile(kb.gitea_repo, oldFilePath);
  if (!oldFile)
    return NextResponse.json({ error: "Page not found" }, { status: 404 });

  const oldRaw = await gitea.getFileContent(kb.gitea_repo, oldFilePath);
  const oldParsed = oldRaw ? parsePage(oldRaw) : { frontmatter: {}, content: "" };

  // Build updated frontmatter: carry over existing fields, add oldSlug to redirects
  const existingRedirects: string[] = Array.isArray(oldParsed.frontmatter.redirects)
    ? oldParsed.frontmatter.redirects
    : [];
  const newRedirects = existingRedirects.includes(oldSlug)
    ? existingRedirects
    : [...existingRedirects, oldSlug];

  const newFrontmatter: Record<string, unknown> = {
    ...oldParsed.frontmatter,
    title: title ?? oldParsed.frontmatter.title ?? newSlug,
    redirects: newRedirects,
  };
  if (icon !== undefined && icon !== null) {
    newFrontmatter.icon = icon;
  }

  const newFilePath = `${newSlug}.md`;
  const newFileContent = serializePage(newFrontmatter as Parameters<typeof serializePage>[0], content ?? oldParsed.content);

  // Create new file
  await gitea.putFile(
    kb.gitea_repo,
    newFilePath,
    newFileContent,
    `Rename ${oldSlug} to ${newSlug}`
  );

  // Delete old file
  await gitea.deleteFile(
    kb.gitea_repo,
    oldFilePath,
    oldFile.sha,
    `Remove ${oldFilePath} after rename to ${newSlug}`
  );

  // Update _sidebar.yaml: rename slug in-place
  const sidebarFile = await gitea.getFile(kb.gitea_repo, "_sidebar.yaml");
  if (sidebarFile) {
    const sidebarRaw = await gitea.getFileContent(kb.gitea_repo, "_sidebar.yaml");
    if (sidebarRaw) {
      const updated = renameSidebarSlug(parseSidebar(sidebarRaw), oldSlug, newSlug);
      await gitea.putFile(
        kb.gitea_repo,
        "_sidebar.yaml",
        serializeSidebar(updated),
        `Rename ${oldSlug} to ${newSlug} in navigation`,
        sidebarFile.sha
      );
    }
  }

  return NextResponse.json({ newPath: newSlug });
}
