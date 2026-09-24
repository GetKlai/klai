"""The calibration run may read only its own tenant's LibreChat, and reports per score strip.

scripts/calibrate_confidence_bands.py sends real questions through a tenant's
widget. Its LibreChat slice widens a short widget history with the same
tenant's internal questions; which database it reads is the tenant boundary.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import calibrate_confidence_bands as cal


def _client_with(openings: list[dict]) -> MagicMock:
    """The aggregation returns each conversation's own first user message."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.__getitem__.return_value.messages.aggregate.return_value = openings
    return client


def test_librechat_questions_are_real_openings_from_the_widget_tenants_own_database():
    """A conversation that opened before the window, or with an over-long first
    message, is skipped, never represented by a later follow-up."""
    recent, old = datetime(2026, 9, 20, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC)
    openings = [
        {"_id": "a", "text": "Hoe stel ik een belgroep in?", "createdAt": recent},
        {"_id": "b", "text": "hoe stel ik een belgroep in?", "createdAt": recent},
        {"_id": "c", "text": "Wat is SRAPS?", "createdAt": recent},
        {"_id": "d", "text": "Opening from May", "createdAt": old},
        {"_id": "e", "text": "x" * 900, "createdAt": recent},
    ]
    client = _client_with(openings)
    with patch.object(cal, "_mongo_client", return_value=client):
        questions = cal._load_librechat_questions("voys", limit=10)

    expected_db = cal.provisioning_names_for_slug("voys", domain=cal.settings.domain).mongodb_database
    client.__getitem__.assert_called_once_with(expected_db)
    assert questions == ["Hoe stel ik een belgroep in?", "Wat is SRAPS?"]


def test_the_score_strip_table_counts_what_the_answer_judge_said(capsys):
    rows = [
        {"top_score": 0.45, "decision": "answer", "unsupported": 0, "verdict": "answered"},
        {"top_score": 0.42, "decision": "partial_answer", "unsupported": 1, "verdict": "not_answered"},
        {"top_score": 0.48, "decision": "partial_answer", "unsupported": 0, "verdict": "partial"},
        {"top_score": 0.9, "decision": "answer", "unsupported": 0, "verdict": "answered"},
        {"top_score": None, "decision": "off_topic", "unsupported": None, "verdict": None},
    ]
    cal._print_score_strips(rows)

    out = capsys.readouterr().out
    strip = next(line for line in out.splitlines() if line.startswith("0.4-0.5"))
    assert strip.split() == ["0.4-0.5", "3", "3", "2/3", "1/3", "1/3", "1/3"]
    assert "4 turns with a score" in out
