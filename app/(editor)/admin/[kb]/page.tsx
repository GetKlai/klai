import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { db } from "@/lib/db";
import { buildNavTree } from "@/lib/gitea";
import { NavTreeEditor } from "@/components/editor/NavTreeEditor";
import Link from "next/link";

export default async function KBEditorPage({
  params,
}: {
  params: Promise<{ kb: string }>;
}) {
  const { kb: kbSlug } = await params;
  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org") ?? "local";

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) notFound();

  const kb = await db.getKB(org.id, kbSlug);
  if (!kb) notFound();

  const tree = await buildNavTree(kb.gitea_repo);

  return (
    <div className="px-8 py-10">
      <div className="flex items-center justify-between mb-6">
        <div>
          <Link href="/admin" className="text-sm text-gray-400 hover:text-gray-600">
            ← All knowledge bases
          </Link>
          <h1 className="text-2xl font-bold mt-1">{kb.name}</h1>
        </div>
        <div className="flex gap-2">
          <Link
            href={`/${kbSlug}`}
            target="_blank"
            className="px-3 py-1.5 text-sm border border-gray-200 rounded hover:bg-gray-50"
          >
            View
          </Link>
          <Link
            href={`/admin/${kbSlug}/edit/`}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            New page
          </Link>
        </div>
      </div>

      <NavTreeEditor
        tree={tree}
        orgSlug={orgSlug}
        kbSlug={kbSlug}
        giteaRepo={kb.gitea_repo}
      />
    </div>
  );
}
