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

const ISSUER = process.env.AUTH_ZITADEL_ISSUER ?? "https://auth.getklai.com";

// JWKS is fetched once and cached by the jose library
const JWKS = createRemoteJWKSet(new URL(`${ISSUER}/oauth/v2/keys`));

export type AuthPayload = JWTPayload & {
  sub: string;
  /** Zitadel org ID claim */
  "urn:zitadel:iam:user:resourceowner:id"?: string;
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
