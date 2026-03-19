import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";
import { headers } from "next/headers";

export default async function EditorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  if (!session) redirect("/api/auth/signin");

  const headersList = await headers();
  const orgSlug = headersList.get("x-docs-org");

  return (
    <div className="flex min-h-screen bg-white">
      <nav className="w-56 shrink-0 border-r border-gray-100 bg-gray-50 px-4 py-6 flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">
          {orgSlug ?? "Editor"}
        </span>
        <a
          href="/admin"
          className="text-sm text-gray-600 hover:text-gray-900 py-1"
        >
          Knowledge bases
        </a>
        <div className="mt-auto pt-4 border-t border-gray-100">
          <span className="text-xs text-gray-400">{session.user?.email}</span>
          <a
            href="/api/auth/signout"
            className="block text-xs text-gray-400 hover:text-red-500 mt-1"
          >
            Sign out
          </a>
        </div>
      </nav>
      <main className="flex-1">{children}</main>
    </div>
  );
}
