import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import { auth } from "@/lib/auth";
import * as gitea from "@/lib/gitea";
import { parsePage } from "@/lib/markdown";
import { BlockEditor } from "@/components/editor/BlockEditor";

export default async function EditPage({
  params,
}: {
  params: Promise<{ kb: string; path: string[] }>;
}) {
  const { kb: kbSlug, path: pathSegments } = await params;
  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org") ?? "local";

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) notFound();

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) notFound();

  const session = await auth();
  if (!session?.user?.id) notFound();

  // Determine file path
  const isNew = pathSegments.length === 0;
  const filePath = isNew ? null : `${pathSegments.join("/")}.md`;

  // Check edit permissions for existing pages
  if (filePath) {
    const pagePath = pathSegments.join("/");
    const restriction = await db.getPageEditRestriction(kb.id, pagePath);
    if (restriction?.user_ids?.length > 0) {
      if (!restriction.user_ids.includes(session.user.id)) {
        notFound();
      }
    }
  }

  // Load existing content
  let initialContent = "";
  let initialTitle = "";
  let sha: string | undefined;

  if (filePath) {
    const file = await gitea.getFile(kb.gitea_repo, filePath);
    if (!file) notFound();
    sha = file.sha;

    const raw = Buffer.from(file.content ?? "", "base64").toString("utf-8");
    const parsed = parsePage(raw);
    initialContent = parsed.content;
    initialTitle = parsed.frontmatter.title ?? "";
  }

  return (
    <BlockEditor
      orgSlug={orgSlug}
      kbSlug={kbSlug}
      filePath={filePath}
      sha={sha}
      initialTitle={initialTitle}
      initialContent={initialContent}
      isNew={isNew}
    />
  );
}
