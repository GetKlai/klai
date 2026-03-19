import { NextRequest, NextResponse } from "next/server";

const BASE_DOMAIN = process.env.NEXT_PUBLIC_BASE_DOMAIN ?? "getklai.com";

/**
 * Resolve the org slug from the incoming hostname.
 *
 * Patterns handled:
 *   voys.getklai.com   → org = "voys"
 *   localhost:3010     → org = "local" (dev only)
 *   docs.voys.nl       → custom domain (future: look up via DB)
 */
function resolveOrg(hostname: string): string | null {
  const host = hostname.split(":")[0];

  if (host === "localhost" || host === "127.0.0.1") return "local";

  if (host.endsWith(`.${BASE_DOMAIN}`)) {
    const sub = host.slice(0, -(BASE_DOMAIN.length + 1));
    const reserved = ["auth", "chat", "grafana", "errors", "llm", "www"];
    if (reserved.includes(sub)) return null;
    return sub;
  }

  return null;
}

export function middleware(request: NextRequest) {
  const host = request.headers.get("host") ?? new URL(request.url).hostname;
  const orgSlug = resolveOrg(host);

  const requestHeaders = new Headers(request.headers);
  if (orgSlug) {
    requestHeaders.set("x-docs-org", orgSlug);
  }

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
