import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { db } from "@/lib/db";
import { auth } from "@/lib/auth";
import { buildNavTree } from "@/lib/gitea";
import * as gitea from "@/lib/gitea";
import { parsePage } from "@/lib/markdown";
import { Sidebar } from "@/components/reader/Sidebar";
import { PageRenderer } from "@/components/reader/PageRenderer";

/**
 * Route: /{kb}/[...slug]  OR  /{kb}/
 *
 * path[0] = kb slug
 * path[1..] = article path segments
 */
export default async function ReaderPage({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  const [kbSlug, ...articleSegments] = path;

  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org");
  if (!orgSlug) notFound();

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) notFound();

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) notFound();

  // Private KB: require authentication
  if (kb.visibility === "private") {
    const session = await auth();
    if (!session) {
      // Middleware will redirect to login; this is a server-side guard
      notFound();
    }
  }

  // Build sidebar navigation tree
  const navTree = await buildNavTree(kb.gitea_repo);

  // Determine article file path
  const articlePath =
    articleSegments.length > 0
      ? `${articleSegments.join("/")}.md`
      : null;

  let content = "";
  let title = kb.name;
  let description = "";

  if (articlePath) {
    const raw = await gitea.getFileContent(kb.gitea_repo, articlePath);
    if (!raw) notFound();

    const parsed = parsePage(raw);
    content = parsed.content;
    title = parsed.frontmatter.title ?? title;
    description = parsed.frontmatter.description ?? "";
  }

  return (
    <div className="flex min-h-screen bg-white">
      <Sidebar
        tree={navTree}
        orgSlug={orgSlug}
        kbSlug={kbSlug}
        kbName={kb.name}
      />
      <main className="flex-1 px-8 py-10 max-w-3xl">
        {articlePath ? (
          <>
            <h1 className="text-3xl font-bold mb-2">{title}</h1>
            {description && (
              <p className="text-gray-500 mb-8">{description}</p>
            )}
            <PageRenderer content={content} />
          </>
        ) : (
          <div>
            <h1 className="text-3xl font-bold mb-2">{kb.name}</h1>
            <p className="text-gray-500">Select a page from the sidebar.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  const [kbSlug, ...articleSegments] = path;

  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org");
  if (!orgSlug) return {};

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) return {};

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) return {};

  if (articleSegments.length > 0) {
    const raw = await gitea.getFileContent(
      kb.gitea_repo,
      `${articleSegments.join("/")}.md`
    );
    if (raw) {
      const { frontmatter } = parsePage(raw);
      return {
        title: `${frontmatter.title ?? kbSlug} — ${kb.name}`,
        description: frontmatter.description,
      };
    }
  }

  return { title: kb.name };
}
