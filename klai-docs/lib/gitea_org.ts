const GITEA_ORG_MAX_LENGTH = 40;
const GITEA_ORG_PREFIX = "org-";
const HASH_LENGTH = 8;

function fnv1a32(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(HASH_LENGTH, "0");
}

export function giteaOrgNameForSlug(slug: string): string {
  const candidate = `${GITEA_ORG_PREFIX}${slug}`;
  if (candidate.length <= GITEA_ORG_MAX_LENGTH) {
    return candidate;
  }

  const hash = fnv1a32(slug);
  const maxSlugPrefixLength =
    GITEA_ORG_MAX_LENGTH - GITEA_ORG_PREFIX.length - 1 - HASH_LENGTH;
  const slugPrefix = slug.slice(0, maxSlugPrefixLength).replace(/-+$/u, "");
  return `${GITEA_ORG_PREFIX}${slugPrefix}-${hash}`;
}
