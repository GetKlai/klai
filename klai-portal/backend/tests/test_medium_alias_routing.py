"""Which Medium alias each portal-api LiteLLM call uses.

Background Medium work (judges, support-case analysis, gap grouping and
folding, the ingest-gap evaluation) runs on klai-judge, which LiteLLM serves
from the Klai organisation's Vibe allowance. Calls made inside a user's chat
turn stay on klai-medium, the regular API route. deploy/litellm/tests pins
what each alias reaches upstream; test_conversation_judge.py pins
conversation_judge_model's default.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

_APP = Path(__file__).resolve().parents[1] / "app"

# Every module that makes a background Medium call. Each must take its model
# from settings.conversation_judge_model rather than name a chat-route alias
# (gap_events.py's "klai-bge-m3" embeddings model is not one).
_BACKGROUND_MEDIUM_MODULES = (
    "services/conversation_judge.py",
    "services/librechat_quality_judge.py",
    "services/support_case_analysis.py",
    "services/support_gap_grouping.py",
    "services/gap_events.py",
    "services/gap_rescorer.py",
    "services/ingest_gap_evaluation.py",
)


def test_in_turn_medium_calls_stay_on_the_regular_route() -> None:
    assert Settings.model_fields["answer_grounding_model"].default == "klai-medium"
    assert Settings.model_fields["retrieval_paraphrase_model"].default == "klai-medium"


def test_no_background_caller_names_a_chat_route_alias_or_in_turn_setting() -> None:
    offenders = []
    for relative in _BACKGROUND_MEDIUM_MODULES:
        source = (_APP / relative).read_text()
        if re.search(r"""["']klai-(fast|primary|medium|large)["']""", source):
            offenders.append(f"{relative}: literal chat-route alias")
        if re.search(r"\b(answer_grounding_model|retrieval_paraphrase_model)\b", source):
            offenders.append(f"{relative}: in-turn model setting")
    assert offenders == []
