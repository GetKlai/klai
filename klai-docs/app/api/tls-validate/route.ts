import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";

/**
 * Caddy on-demand TLS validation endpoint.
 * Caddy calls this before issuing a cert for a custom domain.
 * Returns 200 if the domain is registered, 403 otherwise.
 */
export async function GET(request: NextRequest) {
  const domain = request.nextUrl.searchParams.get("domain");
  if (!domain) return new NextResponse(null, { status: 400 });

  const org = await db.getOrgByCustomDomain(domain);
  if (!org) return new NextResponse(null, { status: 403 });

  return new NextResponse(null, { status: 200 });
}
