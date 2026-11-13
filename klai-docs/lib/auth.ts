/**
 * Caller identity for docs-app API routes.
 *
 * Two kinds of caller reach the API, both from inside the cluster (Caddy
 * answers 404 on /docs/api/*) and both with X-Internal-Secret set to
 * DOCS_INTERNAL_SECRET:
 *
 * - portal-api's BFF proxy: it has checked the user's active membership of the
 *   session's organisation and asserts it in X-Klai-Verified-User-Id /
 *   -Org-Id / -Org-Slug. That user reaches only the asserted organisation.
 * - service callers (portal-api provisioning, klai-knowledge-mcp), naming the
 *   acting user in X-User-ID and, when acting for one organisation, its
 *   Zitadel org id in X-Org-ID.
 *
 * A request without that secret identifies nobody; a session token is not read.
 * Each identification through the proxy assertion logs one
 * `docs_auth_identified` line with the kind only.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";
import { db } from "./db";

export type AuthPayload =
  | { kind: "proxy_assertion"; sub: string; org_id: string; org_slug: string }
  | { kind: "service"; sub: string; org_id?: string };

function logIdentified(kind: AuthPayload["kind"]): void {
  console.info(JSON.stringify({ event: "docs_auth_identified", kind }));
}

function matchesSecret(incoming: string | null, expected: string): boolean {
  if (!incoming) return false;
  // Equal-length digests let timingSafeEqual compare without leaking how much
  // of the secret matched or how long it is.
  const digest = (s: string) => createHash("sha256").update(s).digest();
  return timingSafeEqual(digest(incoming), digest(expected));
}

/**
 * Resolve the caller: with the docs secret, the proxy assertion or X-User-ID
 * names it. Returns null when no caller can be established.
 */
export async function requireAuthOrService(
  request: Request
): Promise<AuthPayload | null> {
  const h = request.headers;
  const secret = process.env.DOCS_INTERNAL_SECRET;
  if (secret && matchesSecret(h.get("x-internal-secret"), secret)) {
    const verifiedUser = h.get("x-klai-verified-user-id");
    if (verifiedUser !== null) {
      const org_id = h.get("x-klai-verified-org-id");
      const org_slug = h.get("x-klai-verified-org-slug");
      if (!verifiedUser || !org_id || !org_slug) return null;
      logIdentified("proxy_assertion");
      return { kind: "proxy_assertion", sub: verifiedUser, org_id, org_slug };
    }
    const sub = h.get("x-user-id");
    if (!sub) return null;
    return { kind: "service", sub, org_id: h.get("x-org-id") ?? undefined };
  }
  return null;
}

/**
 * A caller identified by the proxy assertion reaches only the organisation the
 * portal verified their membership of. A service caller reaches any
 * organisation unless an org id binds it to one.
 */
export function callerMayAccessOrg(
  payload: AuthPayload,
  org: { slug: string; zitadel_org_id: string }
): boolean {
  if (payload.kind === "proxy_assertion") return payload.org_slug === org.slug;
  return !payload.org_id || payload.org_id === org.zitadel_org_id;
}

/**
 * Check if the user has access to a specific KB.
 * Personal KBs are only accessible by their creator.
 * Returns a 403 NextResponse if denied, null if allowed.
 */
export function checkKBAccess(
  kb: { kb_type: string; created_by: string | null; [k: string]: unknown },
  userId: string
): NextResponse | null {
  if (kb.kb_type === "personal" && kb.created_by !== userId) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  return null;
}

type OrgAccessResult =
  | { error: NextResponse; payload?: undefined; org?: undefined }
  | { error?: undefined; payload: AuthPayload; org: { id: string; slug: string; zitadel_org_id: string; [k: string]: unknown } };

/**
 * Verify that the authenticated caller belongs to the org identified by `orgSlug`.
 *
 * Returns `{ payload, org }` on success, or `{ error: NextResponse }` on auth/access failure.
 */
export async function requireOrgAccess(
  request: Request,
  orgSlug: string
): Promise<OrgAccessResult> {
  const payload = await requireAuthOrService(request);
  if (!payload) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) {
    return { error: NextResponse.json({ error: "Not found" }, { status: 404 }) };
  }

  if (!callerMayAccessOrg(payload, org)) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }

  return { payload, org };
}
