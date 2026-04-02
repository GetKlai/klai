import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { buildNavTree } from "@/lib/gitea";

type Params = { org: string; kb: string };

// GET /api/orgs/{org}/kbs/{kb}/tree
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug } = await params;

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Personal KBs are not served via the public tree endpoint
  if (kb.kb_type === "personal") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const tree = await buildNavTree(kb.gitea_repo);
  return NextResponse.json(tree);
}
