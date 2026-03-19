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
  path: string;       // full path from repo root, no leading slash
  type: "file" | "dir";
  children?: NavNode[];
};

import yaml from "js-yaml";

/**
 * Build the navigation tree for a KB by reading _meta.yaml files recursively.
 * Returns the ordered tree used to render the sidebar.
 */
export async function buildNavTree(
  repo: string,
  dirPath = "",
  depth = 0
): Promise<NavNode[]> {
  if (depth > 5) return []; // guard against deep nesting

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

  const nodes: NavNode[] = [];

  // Items in explicit order first, then remaining alphabetically
  const nonMeta = entries.filter((e) => e.name !== "_meta.yaml");
  const orderedEntries = [
    ...order
      .map((slug) => nonMeta.find((e) => e.name === slug || e.name === `${slug}.md`))
      .filter((e): e is GiteaFile => !!e),
    ...nonMeta
      .filter((e) => !order.includes(e.name) && !order.includes(e.name.replace(/\.md$/, "")))
      .sort((a, b) => a.name.localeCompare(b.name)),
  ];

  for (const entry of orderedEntries) {
    const slug = entry.name.replace(/\.md$/, "");
    const path = entry.path;

    if (entry.type === "dir") {
      const children = await buildNavTree(repo, path, depth + 1);
      nodes.push({
        slug,
        title: labels[slug] ?? slug.replace(/-/g, " "),
        path,
        type: "dir",
        children,
      });
    } else if (entry.name.endsWith(".md")) {
      // Read title from frontmatter if available
      const content = await getFileContent(repo, path);
      let title = labels[slug] ?? slug.replace(/-/g, " ");
      if (content) {
        const match = content.match(/^---[\s\S]*?^title:\s*(.+)$/m);
        if (match) title = match[1].trim().replace(/^["']|["']$/g, "");
      }
      nodes.push({ slug, title, path, type: "file" });
    }
  }

  return nodes;
}
