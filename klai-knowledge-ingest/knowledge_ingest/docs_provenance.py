"""Public provenance helpers for Docs pages stored in tenant Gitea repos."""

from __future__ import annotations

from urllib.parse import quote

_GITEA_ORG_PREFIX = "org-"
_DOCS_PUBLIC_DOMAIN = "getklai.com"


def build_docs_source_extra(gitea_repo: str, kb_slug: str, path: str) -> dict[str, str]:
    """Build source metadata for a Docs page.

    Gitea repos are named ``org-{tenant_slug}/{kb_slug}``; the public reader
    lives at ``https://{tenant_slug}.getklai.com/docs/{kb_slug}/{page}``.
    """
    source_url = build_docs_source_url(gitea_repo, kb_slug, path)
    if source_url is None:
        return {}
    return {"source_url": source_url, "source_ref": source_url}


def build_docs_source_url(gitea_repo: str, kb_slug: str, path: str) -> str | None:
    org_slug = _tenant_slug_from_repo(gitea_repo)
    page_slug = _page_slug_from_path(path)
    if org_slug is None or page_slug is None:
        return None
    return (
        f"https://{org_slug}.{_DOCS_PUBLIC_DOMAIN}/docs/"
        f"{quote(kb_slug, safe='')}/{_quote_path(page_slug)}"
    )


def _tenant_slug_from_repo(gitea_repo: str) -> str | None:
    owner = gitea_repo.split("/", 1)[0] if "/" in gitea_repo else ""
    if not owner.startswith(_GITEA_ORG_PREFIX):
        return None
    org_slug = owner.removeprefix(_GITEA_ORG_PREFIX).strip()
    return org_slug or None


def _page_slug_from_path(path: str) -> str | None:
    page_slug = path.removesuffix(".md").strip("/")
    return page_slug or None


def _quote_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/") if part)
