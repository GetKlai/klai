from pathlib import Path

import yaml

from klai_llm_safety import (
    SafetyAction,
    SafetyPhase,
    SafetyRequest,
    SafetySurface,
    check_text,
)

_CORPUS = Path(__file__).parent / "corpus" / "guardrail_cases.yaml"


def test_guardrail_corpus() -> None:
    cases = yaml.safe_load(_CORPUS.read_text())

    for case in cases:
        decision = check_text(
            SafetyRequest(
                text=case["input"],
                phase=SafetyPhase(case["phase"]),
                surface=SafetySurface(case["surface"]),
                locale_hint=case["input"],
            )
        )
        assert decision.action == SafetyAction(case["expected_action"]), case["id"]
