import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { parsePage, parseMeta, serializeMeta } from "@/lib/markdown";

type Params = { org: string; kb: string };

// POST /api/orgs/{org}/kbs/{kb}/upload
// Multipart: file (.md), folder (target folder path, optional)
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const session = await auth();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug } = await params;
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const formData = await request.formData();
  const file = formData.get("file") as File | null;
  const targetFolder = (formData.get("folder") as string | null) ?? "";

  if (!file || !file.name.endsWith(".md")) {
    return NextResponse.json({ error: "Only .md files are accepted" }, { status: 400 });
  }

  const raw = await file.text();
  const { frontmatter } = parsePage(raw);

  const slug = file.name.replace(/\.md$/, "");
  const filePath = targetFolder ? `${targetFolder}/${file.name}` : file.name;

  // Write file to Gitea
  await gitea.putFile(
    kb.gitea_repo,
    filePath,
    raw,
    `Upload ${filePath}`
  );

  // Append slug to _meta.yaml of the target folder
  const metaFilePath = targetFolder ? `${targetFolder}/_meta.yaml` : "_meta.yaml";
  const existing = await gitea.getFile(kb.gitea_repo, metaFilePath);

  const meta = existing
    ? parseMeta(Buffer.from(existing.content ?? "", "base64").toString("utf-8"))
    : {};

  if (!meta.order) meta.order = [];
  if (!meta.order.includes(slug)) meta.order.push(slug);

  await gitea.putFile(
    kb.gitea_repo,
    metaFilePath,
    serializeMeta(meta),
    `Add ${slug} to nav`,
    existing?.sha
  );

  return NextResponse.json({
    path: filePath,
    title: frontmatter.title ?? slug,
  });
}
