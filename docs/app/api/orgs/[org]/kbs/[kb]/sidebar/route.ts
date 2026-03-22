import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { parseSidebar, serializeSidebar, type SidebarEntry } from "@/lib/markdown";

type Params = { org: string; kb: string };

async function resolveKB(orgSlug: string, kbSlug: string) {
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return null;
  const kb = await db.getKB(org.id, kbSlug);
  return kb ? { org, kb } : null;
}

// PUT /api/orgs/{org}/kbs/{kb}/sidebar
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const payload = await requireAuth(request);
  if (!payload) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const body = await request.json();
  const pages: SidebarEntry[] = body.pages ?? [];

  // Read current _sidebar.yaml sha for update vs create
  const existing = await gitea.getFile(resolved.kb.gitea_repo, "_sidebar.yaml");
  const sha = existing?.sha;

  const content = serializeSidebar({ pages });
  await gitea.putFile(
    resolved.kb.gitea_repo,
    "_sidebar.yaml",
    content,
    "Update navigation",
    sha
  );

  return NextResponse.json({ ok: true });
}
