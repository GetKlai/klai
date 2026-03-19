import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { writeTenantCaddyfile } from "@/lib/caddy";
import { slugify } from "@/lib/markdown";

// GET /api/orgs/{org}/kbs
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ org: string }> }
) {
  const { org: orgSlug } = await params;
  const session = await auth();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kbs = await db.getKBsByOrg(org.id);
  return NextResponse.json(kbs);
}

// POST /api/orgs/{org}/kbs
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ org: string }> }
) {
  const { org: orgSlug } = await params;
  const session = await auth();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { name, visibility = "private" } = await request.json();
  if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });

  const slug = slugify(name);
  if (!slug) return NextResponse.json({ error: "Invalid name" }, { status: 400 });

  let org = await db.getOrgBySlug(orgSlug);

  // Auto-provision org record if it doesn't exist yet
  if (!org) {
    const zitadelOrgId = (session as { orgId?: string }).orgId ?? orgSlug;
    org = await db.createOrg(orgSlug, orgSlug, zitadelOrgId);

    // Create Gitea org
    await gitea.createOrg(`org-${orgSlug}`, orgSlug);

    // Write Caddy tenant file so the subdomain routes to docs-app
    await writeTenantCaddyfile(orgSlug, process.env.NEXT_PUBLIC_BASE_DOMAIN ?? "getklai.com");
  }

  const giteaRepo = `org-${orgSlug}/${slug}`;

  // Create Gitea repo
  await gitea.createRepo(`org-${orgSlug}`, slug, name);

  // Seed repo with root _meta.yaml
  await gitea.putFile(
    giteaRepo,
    "_meta.yaml",
    `title: ${name}\norder: []\n`,
    "Initialize knowledge base"
  );

  const kb = await db.createKB(org.id, slug, name, visibility, giteaRepo);
  return NextResponse.json(kb, { status: 201 });
}
