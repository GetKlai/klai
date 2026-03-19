import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/lib/db";
import * as gitea from "@/lib/gitea";
import { serializePage, parsePage } from "@/lib/markdown";

type Params = { org: string; kb: string; path: string[] };

async function resolveKB(orgSlug: string, kbSlug: string) {
  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return null;
  const kb = await db.getKB(org.id, kbSlug);
  return kb ? { org, kb } : null;
}

// GET /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const { org: orgSlug, kb: kbSlug, path } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const filePath = `${path.join("/")}.md`;
  const raw = await gitea.getFileContent(resolved.kb.gitea_repo, filePath);
  if (!raw) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const parsed = parsePage(raw);
  return NextResponse.json(parsed);
}

// PUT /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const session = await auth();
  if (!session?.user?.id)
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug, path } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const pagePath = path.join("/");

  // Check edit permissions
  const restriction = await db.getPageEditRestriction(resolved.kb.id, pagePath);
  if (restriction?.user_ids?.length > 0) {
    if (!restriction.user_ids.includes(session.user.id)) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 });
    }
  }

  const { title, content, sha } = await request.json();
  const filePath = `${pagePath}.md`;

  const frontmatter = { title: title ?? pagePath.split("/").at(-1) };
  const fileContent = serializePage(frontmatter, content ?? "");

  const file = await gitea.getFile(resolved.kb.gitea_repo, filePath);
  const currentSha = file?.sha ?? sha;

  await gitea.putFile(
    resolved.kb.gitea_repo,
    filePath,
    fileContent,
    `Update ${filePath}`,
    currentSha
  );

  return NextResponse.json({ ok: true });
}

// DELETE /api/orgs/{org}/kbs/{kb}/pages/{...path}
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<Params> }
) {
  const session = await auth();
  if (!session?.user?.id)
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { org: orgSlug, kb: kbSlug, path } = await params;
  const resolved = await resolveKB(orgSlug, kbSlug);
  if (!resolved) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const filePath = `${path.join("/")}.md`;
  const file = await gitea.getFile(resolved.kb.gitea_repo, filePath);
  if (!file) return NextResponse.json({ error: "Not found" }, { status: 404 });

  await gitea.deleteFile(
    resolved.kb.gitea_repo,
    filePath,
    file.sha,
    `Delete ${filePath}`
  );

  return NextResponse.json({ ok: true });
}
