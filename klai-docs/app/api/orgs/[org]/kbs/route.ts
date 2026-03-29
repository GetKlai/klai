import { NextRequest, NextResponse } from "next/server";
import { requireAuthOrService } from "@/lib/auth";
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
  const payload = await requireAuthOrService(request);
  if (!payload) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json([], { status: 200 });

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
      (payload["urn:zitadel:iam:user:resourceowner:id"] as string) ?? orgSlug;
    org = await db.createOrg(orgSlug, orgSlug, zitadelOrgId);
    await gitea.createOrg(`org-${orgSlug}`, orgSlug);
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
  await ki.registerKBWebhook(org.id, slug, giteaRepo);
  // Trigger initial index in case the repo already has content (no-op for empty repos)
  await ki.bulkSyncKB(org.id, slug, giteaRepo);

  return NextResponse.json(kb, { status: 201 });
}
