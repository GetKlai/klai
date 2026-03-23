"""
Text chunking for document ingestion.
Splits text into ~512-token chunks with 64-token overlap, preserving paragraph boundaries.
"""
import re

import tiktoken

_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_enc = tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str,
    source_id: str,
    notebook_id: str,
    tenant_id: str,
    base_metadata: dict | None = None,
) -> list[dict]:
    """
    Split text into chunks. Returns list of dicts with keys:
      content, source_id, notebook_id, tenant_id, metadata
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    chunks: list[dict] = []
    current_tokens: list[int] = []
    current_paragraphs: list[str] = []

    def _flush(overlap_tokens: list[int]) -> list[int]:
        if not current_tokens:
            return overlap_tokens
        chunk_text_str = _enc.decode(current_tokens)
        meta = dict(base_metadata or {})
        meta["chunk_index"] = len(chunks)
        chunks.append(
            {
                "content": chunk_text_str,
                "source_id": source_id,
                "notebook_id": notebook_id,
                "tenant_id": tenant_id,
                "metadata": meta,
            }
        )
        # Return last overlap_tokens tokens as seed for next chunk
        return current_tokens[-_OVERLAP_TOKENS:] if len(current_tokens) > _OVERLAP_TOKENS else []

    overlap_seed: list[int] = []

    for para in paragraphs:
        para_tokens = _enc.encode(para)

        if len(overlap_seed) + len(current_tokens) + len(para_tokens) > _CHUNK_TOKENS:
            if current_tokens:
                overlap_seed = _flush(overlap_seed)
                current_tokens = list(overlap_seed)
                current_paragraphs = []

        current_tokens.extend(para_tokens)
        current_paragraphs.append(para)

    if current_tokens:
        _flush(overlap_seed)

    return chunks
