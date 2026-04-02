import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import {
  serializePage,
  parsePage,
  parseSidebar,
  serializeSidebar,
  removeSlugFromSidebar,
} from "@/lib/markdown";

const uuidSchema = z.string().uuid();

const knowledgeFrontmatterSchema = z.object({
  provenance_type: z.enum(["observed", "extracted", "synthesized", "revised"]).optional(),
  assertion_mode: z.enum(["factual", "procedural", "quoted", "belief", "hypothesis"]).optional(),
  synthesis_depth: z.number().int().min(0).max(4).optional(),
  confidence: z.enum(["high", "medium", "low"]).nullable().optional(),
  belief_time_start: z.string().optional(),
  belief_time_end: z.string().nullable().optional(),
  superseded_by: uuidSchema.nullable().optional(),
  derived_from: z.array(uuidSchema).optional(),
  source_note: z.string().optional(),
  tags: z.array(z.string()).optional(),
  created_by: z.string().optional(),
  system_time: z.string().optional(),
}).passthrough();

type Params = { org: string; kb: string; path: string[] };

async function resolveKB(orgSlug: string, kbSlug: string) {
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return null;
  const kb = await db.getKB(org.id, kbSlug);
  return kb ? { org, kb } : null;
}

// GET /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug, path } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Personal KBs are not served via the public reader
  if (resolved.kb.kb_type === "personal") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const filePath = `${path.join("/")}.md`;
  const raw = await gitea.getFileContent(resolved.kb.gitea_repo, filePath);
  if (!raw) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const parsed = parsePage(raw);
  return NextResponse.json(parsed);
}

// PUT /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function PUT(
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

  const pagePath = path.join("/");

  // Check edit permissions
  const restriction = await db.getPageEditRestriction(kb.id, pagePath);
  if (restriction?.user_ids?.length > 0) {
    if (!restriction.user_ids.includes(payload.sub)) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }
  }

  const { title, content, icon, sha, edit_access, frontmatter: extraFm } = await request.json();

  // Validate knowledge model fields if provided
  if (extraFm !== undefined && extraFm !== null) {
    const parsed = knowledgeFrontmatterSchema.safeParse(extraFm);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Invalid frontmatter", details: parsed.error.issues },
        { status: 422 }
      );
    }
  }

  const filePath = `${pagePath}.md`;

  const file = await gitea.getFile(kb.gitea_repo, filePath);
  const currentSha = file?.sha ?? sha;
  const isNewPage = !currentSha;

  // Read existing frontmatter to preserve fields (e.g. id, redirects)
  const existingRaw = !isNewPage ? await gitea.getFileContent(kb.gitea_repo, filePath) : null;
  const existingFm = existingRaw ? parsePage(existingRaw).frontmatter : {};

  // Assign a stable UUID on first save; preserve existing id thereafter
  const pageId = existingFm.id ?? crypto.randomUUID();

  const frontmatter: Record<string, unknown> = {
    ...existingFm,
    // Spread extra knowledge model fields (e.g. from klai-knowledge-mcp) before
    // applying mandatory fields — mandatory fields always win on conflict.
    ...(extraFm && typeof extraFm === "object" ? extraFm : {}),
    id: pageId,
    title: title ?? pagePath.split("/").at(-1),
  };
  if (icon !== undefined && icon !== null) frontmatter.icon = icon;
  if (edit_access !== undefined) frontmatter.edit_access = edit_access;
  const fileContent = serializePage(frontmatter as Parameters<typeof serializePage>[0], content ?? "");

  await gitea.putFile(
    kb.gitea_repo,
    filePath,
    fileContent,
    `Update ${filePath}`,
    currentSha
  );

  // Append new slug to _sidebar.yaml when creating a new page
  if (isNewPage) {
    const sidebarFile = await gitea.getFile(kb.gitea_repo, "_sidebar.yaml");
    if (sidebarFile) {
      const sidebarRaw = await gitea.getFileContent(kb.gitea_repo, "_sidebar.yaml");
      const manifest = sidebarRaw ? parseSidebar(sidebarRaw) : { pages: [] };
      manifest.pages.push({ slug: pagePath });
      await gitea.putFile(
        kb.gitea_repo,
        "_sidebar.yaml",
        serializeSidebar(manifest),
        `Add ${pagePath} to navigation`,
        sidebarFile.sha
      );
    }
  }

  return NextResponse.json({ ok: true });
}

// DELETE /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function DELETE(
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

  const filePath = `${path.join("/")}.md`;
  const file = await gitea.getFile(kb.gitea_repo, filePath);
  if (!file) return NextResponse.json({ error: "Not found" }, { status: 404 });

  await gitea.deleteFile(
    kb.gitea_repo,
    filePath,
    file.sha,
    `Delete ${filePath}`
  );

  // Remove slug from _sidebar.yaml after deleting the page
  const sidebarFile = await gitea.getFile(kb.gitea_repo, "_sidebar.yaml");
  if (sidebarFile) {
    const sidebarRaw = await gitea.getFileContent(kb.gitea_repo, "_sidebar.yaml");
    if (sidebarRaw) {
      const slug = path.join("/");
      const updated = removeSlugFromSidebar(parseSidebar(sidebarRaw), slug);
      await gitea.putFile(
        kb.gitea_repo,
        "_sidebar.yaml",
        serializeSidebar(updated),
        `Remove ${slug} from navigation`,
        sidebarFile.sha
      );
    }
  }

  return NextResponse.json({ ok: true });
}
