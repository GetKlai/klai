import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

const BASE_DOMAIN = process.env.NEXT_PUBLIC_BASE_DOMAIN ?? "getklai.com";

/**
 * Resolve the org slug from the incoming hostname.
 *
 * Patterns handled:
 *   voys.getklai.com        → org = "voys"
 *   docs.voys.nl            → org resolved via X-Docs-Org header (set by Caddy)
 *   localhost:3010          → org = "local" (dev only)
 */
function resolveOrg(hostname: string): string | null {
  // Strip port
  const host = hostname.split(":")[0];

  if (host === "localhost" || host === "127.0.0.1") return "local";

  // Standard subdomain: {org}.getklai.com
  if (host.endsWith(`.${BASE_DOMAIN}`)) {
    const sub = host.slice(0, -(BASE_DOMAIN.length + 1));
    // Reject known non-org subdomains
    const reserved = ["auth", "chat", "grafana", "errors", "llm", "edit", "www"];
    if (reserved.includes(sub)) return null;
    return sub;
  }

  // Custom domain: resolved by Caddy header (future)
  return null;
}

export async function middleware(request: NextRequest) {
  const { pathname, hostname } = new URL(request.url);
  const host = request.headers.get("host") ?? hostname;

  const orgSlug = resolveOrg(host);

  // Clone headers and inject resolved org
  const requestHeaders = new Headers(request.headers);
  if (orgSlug) {
    requestHeaders.set("x-docs-org", orgSlug);
  }

  // Protect editor routes
  if (pathname.startsWith("/admin")) {
    const session = await auth();
    if (!session) {
      const loginUrl = new URL("/api/auth/signin", request.url);
      loginUrl.searchParams.set("callbackUrl", request.url);
      return NextResponse.redirect(loginUrl);
    }
    // Inject org into header for layout/page use
    return NextResponse.next({ request: { headers: requestHeaders } });
  }

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: [
    // Run on all paths except Next.js internals and static files
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
