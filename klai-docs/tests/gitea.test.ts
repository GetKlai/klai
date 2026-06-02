import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFile } from "fs/promises";

async function loadGitea() {
  vi.resetModules();
  return import("../lib/gitea");
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("gitea org metadata", () => {
  it("keeps short org slugs readable", async () => {
    const { giteaOrgNameForSlug } = await import("../lib/gitea_org");

    expect(giteaOrgNameForSlug("yoobi-37568691")).toBe("org-yoobi-37568691");
    expect(giteaOrgNameForSlug("stichting-nieuw-nabuurschap-37568663")).toBe(
      "org-stichting-nieuw-nabuurschap-37568663"
    );
  });

  it("shortens long org slugs to fit Gitea username limits", async () => {
    const { giteaOrgNameForSlug } = await import("../lib/gitea_org");

    const name = giteaOrgNameForSlug("deepblue-security-intelligence-37491434");

    expect(name).toHaveLength(40);
    expect(name).toMatch(/^org-deepblue-security-[a-z-]+-[0-9a-f]{8}$/u);
    expect(giteaOrgNameForSlug("deepblue-security-intelligence-37491434")).toBe(name);
  });

  it("does not hardcode org-prefixed Gitea names in API routes", async () => {
    const routeFiles = [
      "app/api/orgs/[org]/kbs/route.ts",
      "app/api/orgs/[org]/kbs/[kb]/route.ts",
      "app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route.ts",
    ];

    for (const routeFile of routeFiles) {
      const source = await readFile(routeFile, "utf-8");
      expect(source).not.toContain("`org-${orgSlug}`");
    }
  });

  it("stores the Zitadel org id in the Gitea org description on create", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);

    const gitea = await loadGitea();
    await gitea.createOrg("org-klai", "klai", "zitadel-org-123");

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(fetchMock.mock.calls[0][0]).toBe("http://gitea:3000/api/v1/orgs");
    expect(body).toMatchObject({
      username: "org-klai",
      full_name: "klai",
      description: "zitadel-org-123",
      visibility: "private",
    });
  });

  it("repairs missing Gitea org description for existing orgs", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ description: "" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({}),
      });
    vi.stubGlobal("fetch", fetchMock);

    const gitea = await loadGitea();
    await gitea.ensureOrgDescription("org-klai", "zitadel-org-123");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("http://gitea:3000/api/v1/orgs/org-klai");
    expect(fetchMock.mock.calls[1][1].method).toBe("PATCH");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      description: "zitadel-org-123",
    });
  });
});
