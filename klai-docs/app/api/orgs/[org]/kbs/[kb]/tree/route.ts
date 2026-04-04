import { NextRequest, NextResponse } from "next/server";
import { requireAuth, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import { buildNavTree } from "@/lib/gitea";

type Params = { org: string; kb: string };

// GET /api/orgs/{org}/kbs/{kb}/tree
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug } = await params;

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Private and personal KBs require authentication + org membership
  if (kb.kb_type === "personal" || kb.visibility === "private") {
    const payload = await requireAuth(request);
    if (!payload) return NextResponse.json({ error: "Not found" }, { status: 404 });
    if (payload.org_id && payload.org_id !== org.zitadel_org_id) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    const denied = checkKBAccess(kb, payload.sub);
    if (denied) return denied;
  }

  const tree = await buildNavTree(kb.gitea_repo);
  return NextResponse.json(tree);
}
