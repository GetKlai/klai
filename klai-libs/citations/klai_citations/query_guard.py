"""Destructive-rewrite guard for LLM-driven conversational query rewriting.

Shared by two call sites that each run an LLM-based query rewrite ahead of
retrieval, resolving pronouns/ellipsis from conversation history into a
standalone search query:

- ``deploy/litellm/klai_kb_query_rewrite.py`` — the LiteLLM pre-call hook
  (path A, LibreChat chat traffic).
- ``klai-retrieval-api/retrieval_api/services/coreference.py`` — the
  coreference resolver used by paths B/C and as retrieval-api's own
  fallback when the litellm hook did not decide the rewrite.

Both resolvers prompt their LLM to keep the current question's subject and
only use history to resolve pronouns, ellipsis, or follow-up phrases. A
prompt instruction is not a guarantee — this module is the deterministic,
LLM-free backstop shared by both: it rejects a rewrite that drops every
salient token of the original query. That is exactly the failure mode that
caused the original topic-hijack incident: a self-contained question
("Wat weet je over klai?") was rewritten into an unrelated historical topic
and then retrieved low-confidence chunks for the wrong subject.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_DEICTIC_REWRITE_TOKENS = {
    "aanvraag",
    "daar",
    "daarover",
    "dat",
    "deze",
    "die",
    "dit",
    "doe",
    "er",
    "hij",
    "hem",
    "hier",
    "hierover",
    "his",
    "it",
    "its",
    "je",
    "that",
    "them",
    "these",
    "they",
    "this",
    "those",
    "what",
    "welke",
    "wie",
    "zij",
}

_QUERY_REWRITE_STOPWORDS = _DEICTIC_REWRITE_TOKENS | {
    "about",
    "also",
    "and",
    "anything",
    "are",
    "can",
    "could",
    "een",
    "eens",
    "for",
    "from",
    "gaat",
    "give",
    "heb",
    "hebt",
    "heeft",
    "hoe",
    "iets",
    "jij",
    "jullie",
    "kan",
    "kun",
    "kunt",
    "me",
    "meer",
    "met",
    "mij",
    "naar",
    "over",
    "please",
    "status",
    "tell",
    "the",
    "van",
    "voor",
    "wat",
    "weet",
    "with",
    "you",
}


def salient_tokens(text: str) -> set[str]:
    """Return the lowercase content tokens (len >= 3, non-stopword) in *text*."""
    return {
        token
        for token in (match.group(0).lower() for match in _TOKEN_RE.finditer(text))
        if len(token) >= 3 and token not in _QUERY_REWRITE_STOPWORDS
    }


def is_followup_query(text: str) -> bool:
    """Return True when *text* is a pure follow-up: deictic tokens only, no salient subject."""
    tokens = {
        match.group(0).lower() for match in _TOKEN_RE.finditer(text) if match.group(0)
    }
    return bool(tokens & _DEICTIC_REWRITE_TOKENS) and not (
        tokens - _QUERY_REWRITE_STOPWORDS
    )


def rewrite_preserves_subject(raw_query: str, rewritten: str) -> bool:
    """Reject rewrites that drop the current query's explicit subject.

    An LLM rewrite may resolve short follow-ups from history, but a clear
    current question must keep at least one salient token from that
    question. This fails closed for the observed incident class: "Wat weet
    je over klai?" was rewritten to an unrelated Yealink/IP-telefonie query
    and then retrieved low confidence chunks.
    """
    if is_followup_query(raw_query):
        return True
    raw_tokens = salient_tokens(raw_query)
    if not raw_tokens:
        return True
    rewritten_tokens = salient_tokens(rewritten)
    return bool(raw_tokens & rewritten_tokens)
