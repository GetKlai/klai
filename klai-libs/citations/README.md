# klai-citations

Deterministic citation composition helpers for Klai RAG answers.

The LLM may write prose, but it is never trusted as the authority for source
URLs or visible citation labels. Callers pass retrieved chunks; this package
strips model-authored citation artifacts and composes source markers and source
lists from chunk provenance.
