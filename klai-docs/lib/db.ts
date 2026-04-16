import { Pool } from "pg";

// Use individual params to avoid URL-encoding issues with special chars in password.
// DATABASE_URL is kept as a fallback for local dev convenience.
const pool = new Pool(
  process.env.POSTGRES_HOST
    ? {
        host: process.env.POSTGRES_HOST,
        database: process.env.POSTGRES_DATABASE ?? "klai",
        user: process.env.POSTGRES_USER ?? "klai",
        password: process.env.POSTGRES_PASSWORD,
        port: Number(process.env.POSTGRES_PORT ?? 5432),
        max: 10,
        idleTimeoutMillis: 30_000,
      }
    : {
        connectionString: process.env.DATABASE_URL,
        max: 10,
        idleTimeoutMillis: 30_000,
      }
);

export const db = {
  query: pool.query.bind(pool),

  async getOrgBySlug(slug: string) {
    const { rows } = await pool.query(
      "SELECT * FROM docs.organizations WHERE slug = $1",
      [slug]
    );
    return rows[0] ?? null;
  },

  async getOrgByCustomDomain(domain: string) {
    const { rows } = await pool.query(
      `SELECT o.* FROM docs.organizations o
       JOIN docs.custom_domains cd ON cd.org_id = o.id
       WHERE cd.domain = $1 AND cd.verified_at IS NOT NULL`,
      [domain]
    );
    return rows[0] ?? null;
  },

  async getKBsByOrg(orgId: string, userId?: string) {
    const { rows } = await pool.query(
      `SELECT * FROM docs.knowledge_bases
       WHERE org_id = $1
         AND (kb_type = 'org' OR created_by = $2)
       ORDER BY created_at`,
      [orgId, userId ?? null]
    );
    return rows;
  },

  async getKB(orgId: string, slug: string) {
    const { rows } = await pool.query(
      "SELECT * FROM docs.knowledge_bases WHERE org_id = $1 AND slug = $2",
      [orgId, slug]
    );
    return rows[0] ?? null;
  },

  async createOrg(slug: string, name: string, zitadelOrgId: string) {
    const gitea_org_name = `org-${slug}`;
    const { rows } = await pool.query(
      `INSERT INTO docs.organizations (slug, name, zitadel_org_id, gitea_org_name)
       VALUES ($1, $2, $3, $4)
       RETURNING *`,
      [slug, name, zitadelOrgId, gitea_org_name]
    );
    return rows[0];
  },

  async createKB(
    orgId: string,
    slug: string,
    name: string,
    visibility: "public" | "private",
    giteaRepo: string,
    kbType: "org" | "personal" = "org",
    createdBy?: string
  ) {
    const { rows } = await pool.query(
      `INSERT INTO docs.knowledge_bases (org_id, slug, name, visibility, gitea_repo, kb_type, created_by)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING *`,
      [orgId, slug, name, visibility, giteaRepo, kbType, createdBy ?? null]
    );
    return rows[0];
  },

  async getPageEditRestriction(kbId: string, pagePath: string) {
    const { rows } = await pool.query(
      "SELECT * FROM docs.page_edit_restrictions WHERE kb_id = $1 AND page_path = $2",
      [kbId, pagePath]
    );
    return rows[0] ?? null;
  },

  async setPageEditRestriction(kbId: string, pagePath: string, userIds: string[]) {
    await pool.query(
      `INSERT INTO docs.page_edit_restrictions (kb_id, page_path, user_ids)
       VALUES ($1, $2, $3)
       ON CONFLICT (kb_id, page_path) DO UPDATE SET user_ids = $3`,
      [kbId, pagePath, userIds]
    );
  },

  /**
   * Check if an idempotency key has been used for a KB.
   * Returns the page_slug of the previously created page, or null if key is new.
   * Keys older than 24h are treated as expired (ignored).
   */
  async getIdempotencyKey(kbId: string, key: string): Promise<string | null> {
    const { rows } = await pool.query(
      `SELECT page_slug FROM docs.idempotency_keys
       WHERE kb_id = $1 AND key = $2
         AND created_at > now() - interval '24 hours'`,
      [kbId, key]
    );
    return rows[0]?.page_slug ?? null;
  },

  /**
   * Store an idempotency key for a page creation.
   * No-op on conflict (key already stored — concurrent duplicate request).
   */
  async storeIdempotencyKey(kbId: string, key: string, pageSlug: string): Promise<void> {
    await pool.query(
      `INSERT INTO docs.idempotency_keys (kb_id, key, page_slug)
       VALUES ($1, $2, $3)
       ON CONFLICT (kb_id, key) DO NOTHING`,
      [kbId, key, pageSlug]
    );
  },
};
