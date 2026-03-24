/**
 * Knowledge-ingest service client.
 * Used by Docs for KB lifecycle events (delete, visibility change).
 * Page-level sync is handled automatically via Gitea webhooks.
 */

const KNOWLEDGE_INGEST_URL =
  process.env.KNOWLEDGE_INGEST_URL ?? "http://knowledge-ingest:8000";
const KNOWLEDGE_INGEST_SECRET = process.env.KNOWLEDGE_INGEST_SECRET ?? "";

async function kiFetch(path: string, init: RequestInit): Promise<void> {
  try {
    const res = await fetch(`${KNOWLEDGE_INGEST_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Secret": KNOWLEDGE_INGEST_SECRET,
        ...init.headers,
      },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      console.warn(
        `[knowledge-ingest] ${init.method} ${path} → ${res.status}: ${body}`
      );
    }
  } catch (e) {
    console.warn(
      `[knowledge-ingest] ${init.method} ${path} failed: ${e instanceof Error ? e.message : e}`
    );
  }
}

/**
 * Delete all Qdrant chunks for a knowledge base.
 * Call when a KB is deleted from Docs.
 */
export async function deleteKB(orgId: string, kbSlug: string): Promise<void> {
  await kiFetch(
    `/ingest/v1/kb?org_id=${encodeURIComponent(orgId)}&kb_slug=${encodeURIComponent(kbSlug)}`,
    { method: "DELETE" }
  );
}

/**
 * Update visibility for all Qdrant chunks in a knowledge base.
 * Call when a KB's visibility changes (public ↔ private).
 */
export async function updateKBVisibility(
  orgId: string,
  kbSlug: string,
  visibility: "public" | "private"
): Promise<void> {
  await kiFetch("/ingest/v1/kb/visibility", {
    method: "PATCH",
    body: JSON.stringify({ org_id: orgId, kb_slug: kbSlug, visibility }),
  });
}
