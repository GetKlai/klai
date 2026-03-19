import NextAuth from "next-auth";
import type { Provider } from "next-auth/providers";

/**
 * Zitadel OIDC provider configuration.
 * Uses the generic OAuth2 provider since next-auth v5 doesn't ship a Zitadel preset.
 */
const ZitadelProvider: Provider = {
  id: "zitadel",
  name: "Zitadel",
  type: "oidc",
  issuer: process.env.AUTH_ZITADEL_ISSUER,
  clientId: process.env.AUTH_ZITADEL_CLIENT_ID,
  clientSecret: process.env.AUTH_ZITADEL_CLIENT_SECRET,
  authorization: { params: { scope: "openid profile email" } },
  checks: ["pkce", "state"],
  // Map Zitadel claims to NextAuth session
  profile(profile) {
    return {
      id: profile.sub,
      name: profile.name ?? profile.preferred_username,
      email: profile.email,
      image: profile.picture ?? null,
      orgId: (profile["urn:zitadel:iam:user:resourceowner:id"] as string) ?? null,
    };
  },
};

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [ZitadelProvider],
  callbacks: {
    jwt({ token, profile }) {
      if (profile) {
        token.userId = profile.sub as string;
        // Zitadel puts org ID in this claim
        token.orgId =
          (profile["urn:zitadel:iam:user:resourceowner:id"] as string) ?? null;
      }
      return token;
    },
    session({ session, token }) {
      if (token) {
        session.user.id = token.userId as string;
        session.orgId = (token.orgId as string | null) ?? null;
      }
      return session;
    },
  },
  pages: {
    signIn: "/admin/login",
  },
});

// Extend NextAuth types
declare module "next-auth" {
  interface Session {
    orgId: string | null;
  }
}
