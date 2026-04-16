import { NextRequest, NextResponse } from "next/server";
import { requireOrgAccess, checkKBAccess } from "@/lib/auth";
import { db } from "@/lib/db";
import { buildPageIndex } from "@/lib/page-index";

export type { PageIndexEntry } from "@/lib/page-index";

type Params = { org: string; kb: string };

// GET /api/orgs/{org}/kbs/{kb}/page-index
// Returns all pages with their stable id, slug, and title.
// Pages without an id have id: null; the real UUID is assigned on the next save.
export async function GET(
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

  const entries = await buildPageIndex(kb.gitea_repo);
  return NextResponse.json(entries);
}
