import { NextRequest, NextResponse } from "next/server";
import { requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { serializeSidebar, type SidebarEntry } from "@/lib/markdown";

type Params = { org: string; kb: string };

// PUT /api/orgs/{org}/kbs/{kb}/sidebar
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug } = await params;
  const access = await requireOrgAccess(request, orgSlug);
  if (access.error) return access.error;
  const { payload, org } = access;

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });
  const denied = checkKBAccess(kb, payload.sub);
  if (denied) return denied;

  const body = await request.json();
  const pages: SidebarEntry[] = body.pages ?? [];

  // Read current _sidebar.yaml sha for update vs create
  const existing = await gitea.getFile(kb.gitea_repo, "_sidebar.yaml");
  const sha = existing?.sha;

  const content = serializeSidebar({ pages });
  await gitea.putFile(
    kb.gitea_repo,
    "_sidebar.yaml",
    content,
    "Update navigation",
    sha
  );

  return NextResponse.json({ ok: true });
}
