import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { parseMeta, serializeMeta } from "@/lib/markdown";

type Params = { org: string; kb: string; path: string[] };

// PUT /api/orgs/{org}/kbs/{kb}/meta/{...folder}
// Body: { order: string[] }
// Updates _meta.yaml for a folder to persist drag-drop reorder.
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const session = await auth();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug, path } = await params;
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const { order } = await request.json() as { order: string[] };
  if (!Array.isArray(order)) {
    return NextResponse.json({ error: "order must be an array" }, { status: 400 });
  }

  // Folder path in repo (empty string = repo root)
  const folderPath = path.join("/");
  const metaFilePath = folderPath ? `${folderPath}/_meta.yaml` : "_meta.yaml";

  const existing = await gitea.getFile(kb.gitea_repo, metaFilePath);
  let meta = existing
    ? parseMeta(Buffer.from(existing.content ?? "", "base64").toString("utf-8"))
    : {};

  meta.order = order;

  await gitea.putFile(
    kb.gitea_repo,
    metaFilePath,
    serializeMeta(meta),
    `Reorder ${folderPath || "root"}`,
    existing?.sha
  );

  return NextResponse.json({ ok: true });
}
