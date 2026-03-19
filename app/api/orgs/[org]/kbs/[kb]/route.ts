import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ org: string; kb: string }> }
) {
  const payload = await requireAuth(request);
  if (!payload) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug } = await params;
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Delete Gitea repo
  await gitea.deleteRepo(`org-${orgSlug}`, kbSlug);

  // Delete from DB (cascades to knowledge_bases, page_edit_restrictions)
  await db.query("DELETE FROM docs.knowledge_bases WHERE id = $1", [kb.id]);

  return NextResponse.json({ ok: true });
}
