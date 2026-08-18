"""Drift guard: every direct Mistral caller in deploy/litellm/ must throttle.

2026-08-18 incident: klai_kb_query_rewrite.py called Mistral directly,
bypassing the litellm proxy's own klai-fast/klai-primary rpm accounting
entirely. This was invisible to knowledge-ingest's independent
shared_klai_fast_limiter (a different process/package) — production saw
1000+ RouterRateLimitError/429 events in a single hour of real chat traffic.

Mirrors the exact same guard already proven in
klai-knowledge-ingest/tests/test_llm_throttle.py::
TestChatCompletionsThrottleDriftGuard — same failure class, same mechanical
fix, so it cannot recur a third time in either codebase without a test
failing immediately.
"""

from __future__ import annotations

from pathlib import Path

_LITELLM_ROOT = Path(__file__).resolve().parent.parent


def test_every_chat_completions_caller_uses_direct_mistral_limiter() -> None:
    offenders: list[str] = []

    for path in sorted(_LITELLM_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "chat/completions" in source and "direct_mistral_limiter" not in source:
            offenders.append(path.name)

    assert not offenders, (
        "These files POST to a chat/completions-shaped endpoint without "
        "acquiring from direct_mistral_limiter() -- they bypass the shared "
        f"Mistral rate budget: {offenders}"
    )
