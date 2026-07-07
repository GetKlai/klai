"""Page-context URL matching + same-page score boost for the /retrieve pipeline.

A self-contained cluster: canonicalise an http(s) URL, decide whether a source
URL sits in the same page-context subtree, and boost (and re-sort) the chunks
whose source matches the caller's current page. Lifted out of ``retrieve.py``
(behavior-preserving). The two URL helpers and the tuning constant are internal
to this module; ``retrieve`` re-imports only ``_apply_page_context_boost`` so
the orchestrator call sites and the
``from retrieval_api.api.retrieve import _apply_page_context_boost`` test import
are unchanged.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from retrieval_api.util.scores import ranking_score

_PAGE_CONTEXT_SCORE_BOOST = 1.08


def _normalise_page_context_url(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def _same_page_context_path(source_url: str, page_url: str) -> bool:
    source = urlparse(source_url)
    page = urlparse(page_url)
    if source.scheme != page.scheme or source.netloc != page.netloc:
        return False
    source_path = source.path.rstrip("/") or "/"
    page_path = page.path.rstrip("/") or "/"
    if source_path == "/" or page_path == "/":
        return source_path == page_path
    return source_path.startswith(f"{page_path}/") or page_path.startswith(f"{source_path}/")


def _apply_page_context_boost(
    chunks: list[dict],
    page_context: dict[str, str] | None,
    *,
    mark: bool = True,
) -> tuple[list[dict], int]:
    page_url = _normalise_page_context_url((page_context or {}).get("url"))
    if not page_url:
        return chunks, 0

    boosted_count = 0
    for chunk in chunks:
        source_url = _normalise_page_context_url(chunk.get("source_url"))
        if not source_url:
            continue
        if source_url != page_url and not _same_page_context_path(source_url, page_url):
            continue

        boosted_count += 1
        # ``final_rank_score`` is only present when the ranking contract is
        # active (REQ-RANK-01) — the boost then mutates the single ranking
        # truth. In shadow mode the field is absent and the boost hits the
        # pre-contract targets (reranker_score post-rerank, raw score on the
        # pre-rerank candidate pass), keeping serving byte-identical.
        score_key = (
            "final_rank_score"
            if isinstance(chunk.get("final_rank_score"), (int, float))
            else "reranker_score"
            if isinstance(chunk.get("reranker_score"), (int, float))
            else "score"
        )
        if isinstance(chunk.get(score_key), (int, float)):
            boosted_score = chunk[score_key] * _PAGE_CONTEXT_SCORE_BOOST
            chunk[score_key] = (
                min(boosted_score, 1.0)
                if score_key in {"final_rank_score", "reranker_score"}
                else boosted_score
            )
            if mark:
                chunk["_page_context_boosted"] = True

    if boosted_count:
        chunks.sort(
            key=lambda c: ranking_score(c, "reranker_score", "score"),
            reverse=True,
        )
    return chunks, boosted_count
