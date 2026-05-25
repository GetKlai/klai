import { beforeEach, describe, expect, it, vi } from "vitest";

async function loadGitea() {
  vi.resetModules();
  return import("../lib/gitea");
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("gitea org metadata", () => {
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
