import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { requireAuthOrService, requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import {
  serializePage,
  parsePage,
  parseSidebar,
  serializeSidebar,
  removeSlugFromSidebar,
} from "@/lib/markdown";
import { buildPageIndex } from "@/lib/page-index";

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
// If path is a legacy slug (not a UUID), returns 308 redirect to the UUID-based path.
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug, path } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Private and personal KBs require authentication + org membership
  if (resolved.kb.kb_type === "personal" || resolved.kb.visibility === "private") {
    const payload = await requireAuthOrService(req);
    if (!payload) return NextResponse.json({ error: "Not found" }, { status: 404 });
    if (payload.org_id && payload.org_id !== resolved.org.zitadel_org_id) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    const denied = checkKBAccess(resolved.kb, payload.sub);
    if (denied) return denied;
  }

  const pagePath = path.join("/");

  // REQ-EVT-05: Support both UUID and slug-based paths.
  // If the path is a UUID, resolve it to a slug via the page index.
  // If the path is already a slug, use it directly.
  const isUuid = uuidSchema.safeParse(pagePath).success;
  let resolvedPath = pagePath;
  if (isUuid) {
    const entries = await buildPageIndex(resolved.kb.gitea_repo);
    const entry = entries.find((e) => e.id === pagePath);
    if (!entry) return NextResponse.json({ error: "Not found" }, { status: 404 });
    resolvedPath = entry.slug;
  }

  const filePath = `${resolvedPath}.md`;
  const raw = await gitea.getFileContent(resolved.kb.gitea_repo, filePath);
  if (!raw) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const parsed = parsePage(raw);
  return NextResponse.json(parsed);
}

// PUT /api/orgs/{org}/kbs/{kb}/pages/{...path}
// For NEW pages: requires Idempotency-Key header, returns { page, pageIndex }.
// For existing page updates: Idempotency-Key is optional, returns { ok: true }.
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

  const idempotencyKey = request.headers.get("Idempotency-Key");

  const filePath = `${pagePath}.md`;
  const file = await gitea.getFile(kb.gitea_repo, filePath);

  // REQ-UNW-03: For new pages, Idempotency-Key is required.
  const isNewPage = !file?.sha;
  if (isNewPage && !idempotencyKey) {
    return NextResponse.json(
      { error: "Idempotency-Key header is required for page creation" },
      { status: 400 }
    );
  }

  // REQ-STA-01: Idempotency dedup — if key was used before, return the existing page.
  if (isNewPage && idempotencyKey) {
    const existingSlug = await db.getIdempotencyKey(kb.id, idempotencyKey);
    if (existingSlug !== null) {
      // Key already used: return the existing page + fresh pageIndex
      const existingFilePath = `${existingSlug}.md`;
      const existingRaw = await gitea.getFileContent(kb.gitea_repo, existingFilePath);
      const existingParsed = existingRaw ? parsePage(existingRaw) : null;
      const pageIndex = await buildPageIndex(kb.gitea_repo);
      return NextResponse.json({
        page: {
          id: existingParsed?.frontmatter.id ?? null,
          slug: existingSlug,
          title: existingParsed?.frontmatter.title ?? existingSlug,
          icon: existingParsed?.frontmatter.icon ?? null,
        },
        pageIndex,
        idempotent: true,
      });
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

  const currentSha = file?.sha ?? sha;

  // Read existing frontmatter to preserve fields (e.g. id, redirects)
  const existingRaw = !isNewPage ? await gitea.getFileContent(kb.gitea_repo, filePath) : null;
  const existingFm = existingRaw ? parsePage(existingRaw).frontmatter : {};

  // Assign a stable UUID on first save; preserve existing id thereafter
  const pageId = existingFm.id ?? crypto.randomUUID();

  const frontmatter: Record<string, unknown> = {
    ...existingFm,
    // Spread extra knowledge model fields before mandatory fields — mandatory always wins.
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

    // Store idempotency key to prevent duplicate creation on retry
    if (idempotencyKey) {
      await db.storeIdempotencyKey(kb.id, idempotencyKey, pagePath);
    }

    // REQ-EVT-02: Return { page, pageIndex } so frontend can update synchronously
    const pageIndex = await buildPageIndex(kb.gitea_repo);
    return NextResponse.json({
      page: {
        id: pageId,
        slug: pagePath,
        title: (title ?? pagePath.split("/").at(-1)) as string,
        icon: (icon ?? null) as string | null,
      },
      pageIndex,
    });
  }

  // Existing page update: maintain backward-compatible response
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
