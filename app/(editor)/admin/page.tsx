import { headers } from "next/headers";
import { db } from "@/lib/db";
import Link from "next/link";

export default async function AdminPage() {
  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org") ?? "local";

  const org = await db.getOrgBySlug(orgSlug);
  const kbs = org ? await db.getKBsByOrg(org.id) : [];

  return (
    <div className="px-8 py-10 max-w-2xl">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-bold">Knowledge bases</h1>
        <Link
          href="/admin/new"
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
        >
          New knowledge base
        </Link>
      </div>

      {kbs.length === 0 ? (
        <p className="text-gray-500">No knowledge bases yet.</p>
      ) : (
        <ul className="divide-y divide-gray-100 border border-gray-100 rounded">
          {kbs.map((kb: { id: string; slug: string; name: string; visibility: string }) => (
            <li key={kb.id} className="flex items-center justify-between px-4 py-3">
              <div>
                <Link
                  href={`/admin/${kb.slug}`}
                  className="font-medium text-gray-900 hover:text-blue-600"
                >
                  {kb.name}
                </Link>
                <span className="ml-2 text-xs text-gray-400">/{kb.slug}/</span>
              </div>
              <span
                className={`text-xs px-2 py-0.5 rounded-full ${
                  kb.visibility === "public"
                    ? "bg-green-50 text-green-700"
                    : "bg-gray-100 text-gray-500"
                }`}
              >
                {kb.visibility}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
