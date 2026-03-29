/**
 * Gitea REST API client.
 * All content operations go through this module.
 */

const GITEA_URL = process.env.GITEA_URL ?? "http://gitea:3000";
const GITEA_TOKEN = process.env.GITEA_ADMIN_TOKEN ?? "";

type GiteaFile = {
  name: string;
  path: string;
  sha: string;
  size: number;
  type: "file" | "dir";
  content?: string; // base64 encoded, only on single-file GET
};

type GiteaCreateOrg = {
  username: string;
  full_name?: string;
  visibility?: "public" | "private";
};

async function giteaFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${GITEA_URL}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `token ${GITEA_TOKEN}`,
      ...init?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Gitea ${init?.method ?? "GET"} ${path} → ${res.status}: ${body}`);
  }

  // 204 No Content
  if (res.status === 204) return null;
  return res.json();
}

// ─── Organisation & repo management ──────────────────────────────────────────

export async function createOrg(orgName: string, displayName: string) {
  const body: GiteaCreateOrg = {
    username: orgName,
    full_name: displayName,
    visibility: "private",
  };
  return giteaFetch("/orgs", { method: "POST", body: JSON.stringify(body) });
}

export async function createRepo(orgName: string, repoSlug: string, description = "") {
  return giteaFetch(`/orgs/${orgName}/repos`, {
    method: "POST",
    body: JSON.stringify({
      name: repoSlug,
      description,
      private: true,
      auto_init: false,
    }),
  });
}

export async function deleteRepo(orgName: string, repoSlug: string) {
  return giteaFetch(`/repos/${orgName}/${repoSlug}`, { method: "DELETE" });
}

export async function createRepoWebhook(
  orgName: string,
  repoName: string,
  webhookUrl: string
): Promise<void> {
  try {
    await giteaFetch(`/repos/${orgName}/${repoName}/hooks`, {
      method: "POST",
      body: JSON.stringify({
        type: "gitea",
        config: {
          url: webhookUrl,
          content_type: "json",
        },
        events: ["push"],
        active: true,
      }),
    });
  } catch (e) {
    console.warn(
      `[gitea] Failed to create webhook for ${orgName}/${repoName}: ${e instanceof Error ? e.message : e}`
    );
  }
}

// ─── File operations ──────────────────────────────────────────────────────────

export async function getFile(
  repo: string,
  filePath: string
): Promise<GiteaFile | null> {
  try {
    return await giteaFetch(`/repos/${repo}/contents/${filePath}`);
  } catch (e: unknown) {
    if (e instanceof Error && e.message.includes("404")) return null;
    throw e;
  }
}

export async function getFileContent(repo: string, filePath: string): Promise<string | null> {
  const file = await getFile(repo, filePath);
  if (!file?.content) return null;
  return Buffer.from(file.content, "base64").toString("utf-8");
}

export async function listDir(repo: string, dirPath: string): Promise<GiteaFile[]> {
  try {
    return await giteaFetch(`/repos/${repo}/contents/${dirPath}`);
  } catch (e: unknown) {
    if (e instanceof Error && e.message.includes("404")) return [];
    throw e;
  }
}

export async function putFile(
  repo: string,
  filePath: string,
  content: string,
  message: string,
  sha?: string
) {
  const body: Record<string, unknown> = {
    message,
    content: Buffer.from(content, "utf-8").toString("base64"),
  };
  if (sha) body.sha = sha;

  return giteaFetch(`/repos/${repo}/contents/${filePath}`, {
    method: sha ? "PUT" : "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteFile(
  repo: string,
  filePath: string,
  sha: string,
  message: string
) {
  return giteaFetch(`/repos/${repo}/contents/${filePath}`, {
    method: "DELETE",
    body: JSON.stringify({ message, sha }),
  });
}

// ─── Nav tree helpers ─────────────────────────────────────────────────────────

export type NavNode = {
  slug: string;
  title: string;
  icon?: string;      // emoji from frontmatter
  path: string;       // full path from repo root, no leading slash
  type: "file" | "dir";
  children?: NavNode[];
};

import yaml from "js-yaml";
import { parseSidebar, type SidebarEntry } from "./markdown";

// ─── Sidebar-based nav tree ───────────────────────────────────────────────────

/**
 * Fetch a page's title and icon from its frontmatter.
 * Returns defaults derived from the slug if the file is missing.
 */
async function fetchPageMeta(
  repo: string,
  slug: string
): Promise<{ title: string; icon?: string; path: string }> {
  const filePath = `${slug}.md`;
  const raw = await getFileContent(repo, filePath);
  const defaultTitle = slug.split("/").at(-1)!.replace(/-/g, " ");

  if (!raw) return { title: defaultTitle, path: filePath };

  const titleMatch = raw.match(/^---[\s\S]*?^title:\s*(.+)$/m);
  const iconMatch = raw.match(/^---[\s\S]*?^icon:\s*(.+)$/m);

  const title = titleMatch
    ? titleMatch[1].trim().replace(/^["']|["']$/g, "")
    : defaultTitle;
  const icon = iconMatch
    ? iconMatch[1].trim().replace(/^["']|["']$/g, "")
    : undefined;

  return { title, icon, path: filePath };
}

async function buildNavTreeFromSidebar(
  repo: string,
  entries: SidebarEntry[]
): Promise<NavNode[]> {
  const nodes: NavNode[] = [];

  for (const entry of entries) {
    const { title, icon, path } = await fetchPageMeta(repo, entry.slug);
    const node: NavNode = {
      slug: entry.slug,
      title,
      path,
      type: "file",
      ...(icon ? { icon } : {}),
      ...(entry.children?.length
        ? { children: await buildNavTreeFromSidebar(repo, entry.children) }
        : {}),
    };
    nodes.push(node);
  }

  return nodes;
}

// ─── Legacy _meta.yaml nav tree ───────────────────────────────────────────────

async function buildNavTreeFromMeta(
  repo: string,
  dirPath = "",
  depth = 0
): Promise<NavNode[]> {
  if (depth > 5) return [];

  const entries = await listDir(repo, dirPath);
  const metaFile = entries.find((e) => e.name === "_meta.yaml" && e.type === "file");

  let order: string[] = [];
  let labels: Record<string, string> = {};

  if (metaFile) {
    const raw = await getFileContent(repo, metaFile.path);
    if (raw) {
      const meta = yaml.load(raw) as { order?: string[]; labels?: Record<string, string> };
      order = meta.order ?? [];
      labels = meta.labels ?? {};
    }
  }

  const nonMeta = entries.filter((e) => e.name !== "_meta.yaml");
  const orderedEntries = [
    ...order
      .map((slug) => nonMeta.find((e) => e.name === slug || e.name === `${slug}.md`))
      .filter((e): e is GiteaFile => !!e),
    ...nonMeta
      .filter((e) => !order.includes(e.name) && !order.includes(e.name.replace(/\.md$/, "")))
      .sort((a, b) => a.name.localeCompare(b.name)),
  ];

  const resolved = await Promise.all(
    orderedEntries.map(async (entry) => {
      const slug = entry.name.replace(/\.md$/, "");
      const path = entry.path;

      if (entry.type === "dir") {
        const children = await buildNavTreeFromMeta(repo, path, depth + 1);
        return { slug, title: labels[slug] ?? slug.replace(/-/g, " "), path, type: "dir" as const, children };
      } else if (entry.name.endsWith(".md")) {
        const content = await getFileContent(repo, path);
        let title = labels[slug] ?? slug.replace(/-/g, " ");
        if (content) {
          const match = content.match(/^---[\s\S]*?^title:\s*(.+)$/m);
          if (match) title = match[1].trim().replace(/^["']|["']$/g, "");
        }
        return { slug, title, path, type: "file" as const };
      }
      return null;
    })
  );

  return resolved.filter((n) => n !== null) as NavNode[];
}

// ─── Public entry point ───────────────────────────────────────────────────────

/**
 * Build the navigation tree for a KB.
 * Tries _sidebar.yaml first; falls back to _meta.yaml directory scanning.
 */
export async function buildNavTree(
  repo: string,
  dirPath = "",
  depth = 0
): Promise<NavNode[]> {
  // Only check for _sidebar.yaml at the root level
  if (depth === 0) {
    const sidebarRaw = await getFileContent(repo, "_sidebar.yaml");
    if (sidebarRaw) {
      const manifest = parseSidebar(sidebarRaw);
      return buildNavTreeFromSidebar(repo, manifest.pages);
    }
  }

  return buildNavTreeFromMeta(repo, dirPath, depth);
}
