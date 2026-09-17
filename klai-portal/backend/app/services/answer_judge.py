"""The answer judge, and the one function that decides what the visitor gets.

SPEC-RAG-ANSWER-JUDGES-001 REQ-2 and REQ-3. Nothing on the widget path used to
ask whether the draft answers the question. The composer checks that cited
sources support the text, and the claims check that this module replaces only
looked at drafts WITHOUT a citable source. Conversation #900 on 2026-09-17 is
the case: "factuur betaald en geïncasseerd, hoe storneren?" at retrieval score
0.08 got an answer, because words like "factuur" and "betaald" also appeared
in an article that does not say how to reverse a direct debit.

The judge reads the visitor's question with a short history, the draft with
markers and links stripped (exactly what the visitor would see), and the
articles the answer model received, and returns two enums. Both are enums for a
measured reason. Probed against production klai-fast on 2026-09-17 (six drafts,
three rounds), a boolean ``unsupported_claims`` answered false 18 out of 18
times, including for an invented phone number and an invented price. Asking the
model to list the unsupported statements flagged "maandelijks" and "achteraf",
both in the article, and the refusal text itself. The three-way ``grounding``
below was right 18 out of 18. A boolean for "the draft only asks something
back" was wrong in 3 of 3 rounds on a pure question, so that is decided from
the text instead (see :func:`is_clarifying_question`).

Cost: this is the one new sequential step, bounded at 2.5 s. It replaces the
claims check that ran in the same place with a 4 s bound. The widget answer is
fully buffered before the first content frame, so nothing streams early.

Fail direction, per the spec: a failed judge shows the composed answer when it
has citable sources (those passed the citation firewall already) and the fixed
refusal when it has none, which is what the claims check did on failure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import conversation_excerpt, structured_judge_call

_ANSWER_JUDGE_TIMEOUT_SECONDS = 2.5
# The judge sees at most the articles the model received (top_k, 8 by default),
# each clipped: enough to tell whether the steps asked for are in there, small
# enough that prefill stays inside the 2.5 s budget.
_ARTICLE_MAX_CHARS = 800

_SYSTEM_PROMPT = (
    "You check a draft reply from a company's help chat before the visitor sees it. You get the "
    "conversation, the draft reply to the visitor's LATEST message, and the help articles the draft "
    "was written from. Judge only against those articles, never against what you know yourself. "
    "Return only the required schema.\n\n"
    "grounding — exactly one category. Check every statement about this company (phone numbers, e-mail "
    "addresses, prices, amounts, time frames, steps, settings, features, policy, availability) against "
    "the articles:\n"
    "no_company_statements — the draft states nothing about the company: a question back, saying "
    "something was not found, a greeting or a thank-you.\n"
    "all_in_articles — every statement about the company is in the articles.\n"
    "some_not_in_articles — at least one statement about the company is not in the articles.\n\n"
    "verdict — exactly one category:\n"
    "answered — the draft gives the visitor what they asked for.\n"
    "partial — the draft answers part of the question, or gives a general direction without the "
    "specific step or fact that was asked for.\n"
    "not_answered — the draft does not answer the question: it says the information is not available, "
    "answers a different question than the one asked, or only asks the visitor something back."
)


class AnswerJudgement(BaseModel):
    """The answer judge's verdict on one draft reply."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    grounding: Literal["no_company_statements", "all_in_articles", "some_not_in_articles"]
    verdict: Literal["answered", "partial", "not_answered"]


def _judge_input(messages: list[dict], draft: str, articles: list[tuple[str, str]]) -> str:
    rendered = "\n\n".join(f"### {title}\n{text[:_ARTICLE_MAX_CHARS]}" for title, text in articles) or "(no articles)"
    return f"Conversation:\n{conversation_excerpt(messages)}\n\nDraft reply:\n{draft}\n\nArticles:\n{rendered}"


async def judge_answer(
    *, messages: list[dict], draft: str, articles: list[tuple[str, str]], settings: Settings
) -> AnswerJudgement | None:
    """Judge one draft; ``None`` means the judge failed. Never raises.

    ``messages`` must be the visitor's own conversation, never the rewritten
    retrieval query: the draft is judged as a reply to what the visitor asked.
    """
    return await structured_judge_call(
        name="answer_judge",
        system_prompt=_SYSTEM_PROMPT,
        user_content=_judge_input(messages, draft, articles),
        schema=AnswerJudgement,
        timeout_seconds=_ANSWER_JUDGE_TIMEOUT_SECONDS,
        settings=settings,
    )


AnswerDecision = Literal["answer", "partial_answer", "clarifying_question", "refusal"]


def decide_answer(
    *,
    has_sources: bool,
    escalation: bool,
    conversational: bool,
    clarity: Literal["clear", "ambiguous"] | None,
    draft_is_question: bool,
    judgement: AnswerJudgement | None,
) -> AnswerDecision:
    """What the visitor gets, from both judges; the spec table with its precedence.

    Safety blocks and broad-mode answers never get here. ``answer`` with
    ``has_sources`` false means the model's own text may be shown without
    sources: every path to it has checked ``grounding``.

    ``clarity`` ``None`` is a failed question judge and reads as clear. An
    escalation turn keeps its outcome from before the judges (the composed
    answer, or the uncited reply when it makes no claim), whatever the verdict:
    its reply points to the appointment button and is not meant to answer.
    """
    if judgement is None:
        return "answer" if has_sources else "refusal"
    unsupported = judgement.grounding == "some_not_in_articles"
    may_show = has_sources or not unsupported
    if escalation:
        return "answer" if may_show else "refusal"
    if conversational:
        return "refusal" if unsupported else "answer"
    if clarity == "ambiguous" and judgement.verdict != "answered":
        if draft_is_question and not unsupported:
            return "clarifying_question"
        return "refusal"
    if judgement.verdict == "not_answered" or not may_show:
        return "refusal"
    return "partial_answer" if judgement.verdict == "partial" else "answer"


def is_clarifying_question(text: str) -> bool:
    """The draft ends on a question: what the ambiguous-turn addendum asks the model to write."""
    return text.rstrip().rstrip("*_ ").endswith("?")
