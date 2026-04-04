/**
 * Zitadel Bearer token validation for docs-app API routes.
 *
 * The editor UI lives in the portal SPA (klai-portal) which already has a
 * Zitadel OIDC session. The portal sends the access token as a Bearer header
 * on every API call. This module validates that token against Zitadel's JWKS
 * endpoint — no separate OAuth2 flow needed.
 *
 * For a future standalone version of Klai Docs (outside the portal context),
 * a full OAuth2/PKCE flow using next-auth would be added here alongside the
 * Bearer validation. See docs/architecture.md for details.
 */
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";
import { NextResponse } from "next/server";
import { db } from "./db";

const ISSUER = process.env.AUTH_ZITADEL_ISSUER ?? "https://auth.getklai.com";

// JWKS is fetched once and cached by the jose library
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/oauth/v2/keys`));

export type AuthPayload = JWTPayload & {
  sub: string;
  /** Zitadel org ID claim */
  "urn:zitadel:iam:user:resourceowner:id"?: string;
  /** Resolved org ID — set from JWT claim or X-Org-ID header */
  org_id?: string;
};

/**
 * Validate a Bearer token from the Authorization header.
 * Returns the JWT payload on success, null if missing or invalid.
 */
export async function validateBearer(
  authHeader: string | null
): Promise<AuthPayload | null> {
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7);
  try {
    const { payload } = await jwtVerify(token, JWKS, { issuer: ISSUER });
    return payload as AuthPayload;
  } catch {
    return null;
  }
}

/**
 * Convenience: extract the Bearer token from a Next.js Request and validate it.
 */
export async function requireAuth(
  request: Request
): Promise<AuthPayload | null> {
  return validateBearer(request.headers.get("authorization"));
}

/**
 * Accept either a Zitadel Bearer token OR an internal service secret.
 *
 * Internal service calls (e.g. klai-knowledge-mcp) set:
 *   X-Internal-Secret: <DOCS_INTERNAL_SECRET>
 *   X-User-ID:         <zitadel user UUID>
 *
 * When the secret matches, X-User-ID is trusted as the acting user.
 * Bypasses Zitadel JWT validation for same-cluster service calls only.
 */
export async function requireAuthOrService(
  request: Request
): Promise<AuthPayload | null> {
  const secret = process.env.DOCS_INTERNAL_SECRET;
  if (secret) {
    const incoming = request.headers.get("x-internal-secret");
    if (incoming === secret) {
      const sub = request.headers.get("x-user-id");
      if (!sub) return null;
      const org_id = request.headers.get("x-org-id") ?? undefined;
      return { sub, org_id, iss: "internal-service" } as AuthPayload;
    }
  }
  const payload = await validateBearer(request.headers.get("authorization"));
  if (payload) {
    payload.org_id =
      (payload["urn:zitadel:iam:user:resourceowner:id"] as string) ?? undefined;
  }
  return payload;
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
  if (!payload?.sub) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }

  const org = await db.getOrgBySlug(orgSlug);
  if (!org) {
    return { error: NextResponse.json({ error: "Not found" }, { status: 404 }) };
  }

  // Verify the caller's org matches the requested org
  if (payload.org_id && payload.org_id !== org.zitadel_org_id) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }

  return { payload, org };
}
