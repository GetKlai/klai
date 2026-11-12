import { createSign, generateKeyPairSync } from "node:crypto";
import { NextRequest } from "next/server";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

vi.mock("../lib/db", () => ({
  db: {
    getOrgBySlug: vi.fn(),
    getKB: vi.fn(),
    getKBsByOrg: vi.fn(),
    createOrg: vi.fn(),
  },
}));
vi.mock("../lib/gitea", () => ({
  buildNavTree: vi.fn(async () => []),
  createOrg: vi.fn(),
  ensureOrgDescription: vi.fn(),
  createRepo: vi.fn(),
  putFile: vi.fn(),
  getFile: vi.fn(async () => ({ content: Buffer.from("# Page\n").toString("base64"), sha: "s" })),
}));
vi.mock("../lib/knowledge_ingest", () => ({
  registerKBWebhook: vi.fn(),
  bulkSyncKB: vi.fn(),
}));

import { db } from "../lib/db";

const DOCS_SECRET = "docs-internal-secret-for-tests";
const PORTAL_SECRET = "portal-internal-secret-for-tests";
const ISSUER = "https://auth.example.com";
const CLAIM = "urn:zitadel:iam:user:resourceowner:id";
const ORG_A = { id: "a0000000-0000-4000-8000-000000000001", slug: "org-a", zitadel_org_id: "100000000000000001" };
const ORG_B = { id: "b0000000-0000-4000-8000-000000000002", slug: "org-b", zitadel_org_id: "100000000000000002" };
const MEMBER_A = "200000000000000001";
const MEMBER_A2 = "200000000000000003";

process.env.DOCS_INTERNAL_SECRET = DOCS_SECRET;
process.env.AUTH_ZITADEL_ISSUER = ISSUER;

// A locally generated key served as the issuer's JWKS, so tokens signed here
// pass the Bearer validation without any network access.
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const KID = "test-key";

function signedToken(claims: Record<string, unknown>): string {
  const b64 = (v: object) => Buffer.from(JSON.stringify(v)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const input = `${b64({ alg: "RS256", kid: KID, typ: "JWT" })}.${b64({ iss: ISSUER, iat: now, exp: now + 300, ...claims })}`;
  const signature = createSign("RSA-SHA256").update(input).sign(privateKey).toString("base64url");
  return `${input}.${signature}`;
}

function verified(org: typeof ORG_A, user = MEMBER_A): Record<string, string> {
  return {
    "x-klai-verified-user-id": user,
    "x-klai-verified-org-id": org.zitadel_org_id,
    "x-klai-verified-org-slug": org.slug,
  };
}

// The proxy assertion carried with the docs app's own secret.
function asserted(org: typeof ORG_A, user = MEMBER_A): Record<string, string> {
  return { "x-internal-secret": DOCS_SECRET, ...verified(org, user) };
}

// Exactly the header set the portal proxy forwards today (pinned by
// test_docs_request_for_own_organisation_forwards_unchanged_headers): the
// portal's own secret, the session's Bearer token and the verified headers.
function liveProxy(org: typeof ORG_A, token: string): Record<string, string> {
  return {
    Accept: "application/json",
    Authorization: `Bearer ${token}`,
    "X-Internal-Secret": PORTAL_SECRET,
    "X-Klai-Verified-User-Id": MEMBER_A,
    "X-Klai-Verified-Org-Id": org.zitadel_org_id,
    "X-Klai-Verified-Org-Slug": org.slug,
  };
}

function req(path: string, headers: Record<string, string>, init: { method?: string; body?: string } = {}) {
  return new NextRequest(`http://docs-app.example.com/docs${path}`, { headers, ...init });
}

let auth: typeof import("../lib/auth");
let info: MockInstance<typeof console.info>;

function identifiedKinds(spy: MockInstance<typeof console.info>): string[] {
  return spy.mock.calls
    .map((c) => JSON.parse(String(c[0])) as { event: string; kind: string })
    .filter((e) => e.event === "docs_auth_identified")
    .map((e) => e.kind);
}

beforeAll(async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ keys: [{ ...publicKey.export({ format: "jwk" }), kid: KID, alg: "RS256", use: "sig" }] })
    )
  );
  auth = await import("../lib/auth");
});

beforeEach(() => {
  info = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.mocked(db.getOrgBySlug).mockImplementation(
    async (slug: string) => ([ORG_A, ORG_B].find((o) => o.slug === slug) ?? null) as never
  );
  vi.mocked(db.getKBsByOrg).mockReset().mockResolvedValue([] as never);
  vi.mocked(db.createOrg).mockReset();
});

afterEach(() => {
  info.mockRestore();
});

describe("proxy assertion with the docs secret", () => {
  it("lets the asserted member reach their own organisation", async () => {
    const { error, payload, org } = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", asserted(ORG_A)), "org-a");
    expect(error).toBeUndefined();
    expect(payload?.sub).toBe(MEMBER_A);
    expect(org?.id).toBe(ORG_A.id);
    expect(identifiedKinds(info)).toEqual(["proxy_assertion"]);
  });

  it("refuses the asserted member another organisation with 403", async () => {
    const { error } = await auth.requireOrgAccess(req("/api/orgs/org-b/kbs", asserted(ORG_A)), "org-b");
    expect(error?.status).toBe(403);
  });

  it("refuses an assertion that lacks the organisation", async () => {
    const headers = { "x-internal-secret": DOCS_SECRET, "x-klai-verified-user-id": MEMBER_A };
    const { error } = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", headers), "org-a");
    expect(error?.status).toBe(401);
  });

  it("logs the identification without user or organisation values", async () => {
    await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", asserted(ORG_A)), "org-a");
    const line = String(info.mock.calls[0][0]);
    expect(JSON.parse(line)).toEqual({ event: "docs_auth_identified", kind: "proxy_assertion" });
  });
});

describe("requests from the portal proxy as it runs today", () => {
  it("are identified from the Bearer token, as before", async () => {
    const token = signedToken({ sub: MEMBER_A });
    const { error, payload } = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", liveProxy(ORG_A, token)), "org-a");
    expect(error).toBeUndefined();
    expect(payload?.sub).toBe(MEMBER_A);
    expect(identifiedKinds(info)).toEqual(["bearer"]);
  });

  it("keep the existing organisation-claim comparison of the Bearer path", async () => {
    const token = signedToken({ sub: MEMBER_A, [CLAIM]: ORG_A.zitadel_org_id });
    const own = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", liveProxy(ORG_A, token)), "org-a");
    const other = await auth.requireOrgAccess(req("/api/orgs/org-b/kbs", liveProxy(ORG_B, token)), "org-b");
    expect(own.error).toBeUndefined();
    expect(other.error?.status).toBe(403);
  });

  it("are refused without a valid token, as before", async () => {
    const { error } = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", liveProxy(ORG_A, "not-a-token")), "org-a");
    expect(error?.status).toBe(401);
  });
});

describe("service callers with the docs secret", () => {
  it("reach an organisation when not bound to one (provisioning)", async () => {
    const headers = { "x-internal-secret": DOCS_SECRET, "x-user-id": "system" };
    const { error, payload } = await auth.requireOrgAccess(req("/api/orgs/org-b/kbs", headers), "org-b");
    expect(error).toBeUndefined();
    expect(payload?.sub).toBe("system");
  });

  it("are limited to the organisation named in X-Org-ID", async () => {
    const headers = { "x-internal-secret": DOCS_SECRET, "x-user-id": MEMBER_A, "x-org-id": ORG_A.zitadel_org_id };
    expect((await auth.requireOrgAccess(req("/api/orgs/org-b/kbs", headers), "org-b")).error?.status).toBe(403);
    expect((await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", headers), "org-a")).error).toBeUndefined();
  });

  it("are refused with a wrong secret", async () => {
    const headers = { "x-internal-secret": "not-the-secret", "x-user-id": MEMBER_A };
    const { error } = await auth.requireOrgAccess(req("/api/orgs/org-a/kbs", headers), "org-a");
    expect(error?.status).toBe(401);
  });
});

describe("docs editor routes with the proxy assertion", () => {
  it("list only the caller's own organisation's knowledge bases", async () => {
    const { GET } = await import("../app/api/orgs/[org]/kbs/route");

    const other = await GET(req("/api/orgs/org-b/kbs", asserted(ORG_A)), { params: Promise.resolve({ org: "org-b" }) });
    expect(other.status).toBe(403);
    expect(db.getKBsByOrg).not.toHaveBeenCalled();

    const own = await GET(req("/api/orgs/org-a/kbs", asserted(ORG_A)), { params: Promise.resolve({ org: "org-a" }) });
    expect(own.status).toBe(200);
    expect(db.getKBsByOrg).toHaveBeenCalledWith(ORG_A.id, MEMBER_A);
  });

  it("refuse to create a knowledge base under another organisation's slug", async () => {
    const { POST } = await import("../app/api/orgs/[org]/kbs/route");
    const body = JSON.stringify({ name: "Handbook" });

    for (const slug of ["org-b", "org-unclaimed"]) {
      const res = await POST(req(`/api/orgs/${slug}/kbs`, asserted(ORG_A), { method: "POST", body }), {
        params: Promise.resolve({ org: slug }),
      });
      expect(res.status).toBe(403);
    }
    expect(db.createOrg).not.toHaveBeenCalled();
  });

  it("hide another organisation's private knowledge base and keep public ones readable", async () => {
    const { GET: tree } = await import("../app/api/orgs/[org]/kbs/[kb]/tree/route");
    const { GET: page } = await import("../app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route");
    const kb = { kb_type: "org", visibility: "private", created_by: null, gitea_repo: "org-b/handbook" };
    vi.mocked(db.getKB).mockResolvedValue(kb as never);
    const params = { org: "org-b", kb: "handbook" };

    expect((await tree(req("/api/orgs/org-b/kbs/handbook/tree", asserted(ORG_A)), { params: Promise.resolve(params) })).status).toBe(404);
    expect(
      (await page(req("/api/orgs/org-b/kbs/handbook/pages/intro", asserted(ORG_A)), {
        params: Promise.resolve({ ...params, path: ["intro"] }),
      })).status
    ).toBe(404);
    expect((await tree(req("/api/orgs/org-b/kbs/handbook/tree", asserted(ORG_B)), { params: Promise.resolve(params) })).status).toBe(200);

    vi.mocked(db.getKB).mockResolvedValue({ ...kb, visibility: "public" } as never);
    expect((await tree(req("/api/orgs/org-b/kbs/handbook/tree", {}), { params: Promise.resolve(params) })).status).toBe(200);
  });

  it("keep a personal knowledge base limited to its creator", async () => {
    const { GET: tree } = await import("../app/api/orgs/[org]/kbs/[kb]/tree/route");
    vi.mocked(db.getKB).mockResolvedValue({
      kb_type: "personal",
      visibility: "private",
      created_by: MEMBER_A2,
      gitea_repo: "org-a/personal",
    } as never);
    const params = { org: "org-a", kb: "personal" };

    expect((await tree(req("/api/orgs/org-a/kbs/personal/tree", asserted(ORG_A)), { params: Promise.resolve(params) })).status).toBe(403);
    expect(
      (await tree(req("/api/orgs/org-a/kbs/personal/tree", asserted(ORG_A, MEMBER_A2)), { params: Promise.resolve(params) })).status
    ).toBe(200);
  });
});
