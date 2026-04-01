import { NextRequest, NextResponse } from "next/server";
import { requireOrgAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import * as ki from "@/lib/knowledge_ingest";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ org: string; kb: string }> }
) {
  const { org: orgSlug, kb: kbSlug } = await params;
  const access = await requireOrgAccess(request, orgSlug);
  if (access.error) return access.error;
  const { org } = access;

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // De-register Gitea webhook before deleting the repo
  try {
    await ki.deregisterKBWebhook(org.zitadel_org_id, kbSlug, kb.gitea_repo);
  } catch (e) {
    console.error(`[ki] deregisterKBWebhook ${kbSlug}: ${e instanceof Error ? e.message : e}`);
    // Non-fatal: the Gitea repo is deleted below, stopping future pushes regardless
  }

  // Delete Gitea repo
  await gitea.deleteRepo(`org-${orgSlug}`, kbSlug);

  // Delete from DB (cascades to knowledge_bases, page_edit_restrictions)
  await db.query("DELETE FROM docs.knowledge_bases WHERE id = $1", [kb.id]);

  // Remove all Qdrant chunks for this KB.
  // Non-fatal: the KB is already gone from DB+Gitea; Qdrant is a derived search index.
  // Log as error so orphaned vectors are detectable in monitoring.
  try {
    await ki.deleteKB(org.zitadel_org_id, kbSlug);
  } catch (e) {
    console.error(`[ki] deleteKB ${kbSlug}: ${e instanceof Error ? e.message : e} — Qdrant chunks may be orphaned`);
  }

  return NextResponse.json({ ok: true });
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ org: string; kb: string }> }
) {
  const { org: orgSlug, kb: kbSlug } = await params;
  const access = await requireOrgAccess(request, orgSlug);
  if (access.error) return access.error;
  const { org } = access;

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const body = await request.json().catch(() => ({}));
  const { name, visibility } = body as { name?: string; visibility?: "public" | "private" };

  if (name !== undefined && typeof name !== "string") {
    return NextResponse.json({ error: "Invalid name" }, { status: 400 });
  }
  if (visibility !== undefined && visibility !== "public" && visibility !== "private") {
    return NextResponse.json({ error: "Invalid visibility" }, { status: 400 });
  }

  const newName = name?.trim() ?? kb.name;
  const newVisibility = visibility ?? kb.visibility;

  const { rows } = await db.query(
    `UPDATE docs.knowledge_bases SET name = $1, visibility = $2 WHERE id = $3 RETURNING *`,
    [newName, newVisibility, kb.id]
  );

  // Propagate visibility change to Qdrant
  if (visibility !== undefined && visibility !== kb.visibility) {
    try {
      await ki.updateKBVisibility(org.zitadel_org_id, kbSlug, newVisibility);
    } catch (e) {
      console.error(`[ki] updateKBVisibility ${kbSlug}: ${e instanceof Error ? e.message : e} — Qdrant visibility may be stale`);
    }
  }

  return NextResponse.json(rows[0]);
}
