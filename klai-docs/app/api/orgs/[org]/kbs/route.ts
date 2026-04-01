import { NextRequest, NextResponse } from "next/server";
import { requireAuthOrService, requireOrgAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import * as ki from "@/lib/knowledge_ingest";
import { slugify } from "@/lib/markdown";

// GET /api/orgs/{org}/kbs
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ org: string }> }
) {
  const { org: orgSlug } = await params;
  const access = await requireOrgAccess(request, orgSlug);
  if (access.error) return access.error;
  const { org } = access;

  const kbs = await db.getKBsByOrg(org.id);
  return NextResponse.json(kbs);
}

// POST /api/orgs/{org}/kbs
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ org: string }> }
) {
  const { org: orgSlug } = await params;
  const payload = await requireAuthOrService(request);
  if (!payload) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { name, slug: reqSlug, visibility = "private" } = await request.json();
  if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });

  const slug = reqSlug ?? slugify(name);
  if (!slug) return NextResponse.json({ error: "Invalid name" }, { status: 400 });

  let org = await db.getOrgBySlug(orgSlug);

  // Auto-provision org record if it doesn't exist yet
  if (!org) {
    const zitadelOrgId =
      (payload["urn:zitadel:iam:user:resourceowner:id"] as string) ?? payload.org_id ?? orgSlug;
    // Verify caller's org matches the org being created
    if (payload.org_id && payload.org_id !== zitadelOrgId) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }
    org = await db.createOrg(orgSlug, orgSlug, zitadelOrgId);
    await gitea.createOrg(`org-${orgSlug}`, orgSlug);
  } else {
    // Existing org: verify caller belongs to it
    if (payload.org_id && payload.org_id !== org.zitadel_org_id) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }
  }

  const giteaOrg = `org-${orgSlug}`;
  const giteaRepo = `${giteaOrg}/${slug}`;
  await gitea.createRepo(giteaOrg, slug, name);

  await gitea.putFile(
    giteaRepo,
    "_meta.yaml",
    `title: ${name}\norder: []\n`,
    "Initialize knowledge base"
  );
  await gitea.putFile(
    giteaRepo,
    "_sidebar.yaml",
    "pages: []\n",
    "Initialize navigation"
  );

  const kb = await db.createKB(org.id, slug, name, visibility, giteaRepo);

  // Register Gitea webhook via knowledge-ingest (owns the HMAC secret and webhook lifecycle)
  // Use zitadel_org_id — knowledge-ingest uses Zitadel org ID as the org namespace in Qdrant.
  try {
    await ki.registerKBWebhook(org.zitadel_org_id, slug, giteaRepo);
  } catch (e) {
    console.error(`[ki] registerKBWebhook ${slug}: ${e instanceof Error ? e.message : e}`);
    throw e; // Fatal: without webhook registration the KB will not be indexed on push
  }
  // Trigger initial index in case the repo already has content (no-op for empty repos)
  try {
    await ki.bulkSyncKB(org.zitadel_org_id, slug, giteaRepo);
  } catch (e) {
    console.error(`[ki] bulkSyncKB ${slug}: ${e instanceof Error ? e.message : e}`);
    // Non-fatal: KB is created and webhook is registered; sync can be retried manually
  }

  return NextResponse.json(kb, { status: 201 });
}
