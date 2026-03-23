"""
Unit tests for transcript_filter.filter_segments.
Pure function tests -- no DB, no HTTP.
"""

from app.services.transcript_filter import filter_segments


def _seg(text: str, speaker: str | None = "Alice", start: float = 0.0, end: float = 5.0) -> dict:
    return {"text": text, "speaker": speaker, "start": start, "end": end}


# -- Empty / punctuation-only -------------------------------------------------


def test_empty_text_removed() -> None:
    assert filter_segments([_seg("")]) == []


def test_whitespace_only_removed() -> None:
    assert filter_segments([_seg("   ")]) == []


def test_punctuation_only_removed() -> None:
    for text in ["...", ".", ",", "  .  ", "\u2014", "\u2026"]:
        assert filter_segments([_seg(text)]) == [], f"Expected {text!r} to be removed"


def test_valid_text_passes() -> None:
    seg = _seg("Hello everyone")
    assert filter_segments([seg]) == [seg]


# -- Subtitle watermarks ------------------------------------------------------


def test_sous_titrage_removed() -> None:
    assert filter_segments([_seg("Sous-titrage ST' 501", speaker=None)]) == []


def test_st_number_removed() -> None:
    assert filter_segments([_seg("ST' 234", speaker=None)]) == []


def test_sous_titrage_case_insensitive() -> None:
    assert filter_segments([_seg("sous-titrage MFP", speaker=None)]) == []


# -- Short speakerless segments -----------------------------------------------


def test_short_speakerless_removed() -> None:
    seg = {"text": "Hmm", "speaker": None, "start": 10.0, "end": 11.5}
    assert filter_segments([seg]) == []


def test_short_with_speaker_kept() -> None:
    seg = {"text": "Yes", "speaker": "Alice", "start": 10.0, "end": 11.5}
    assert filter_segments([seg]) == [seg]


def test_long_speakerless_kept() -> None:
    seg = {"text": "Background noise detected", "speaker": None, "start": 0.0, "end": 3.0}
    assert filter_segments([seg]) == [seg]


# -- Consecutive duplicates ---------------------------------------------------


def test_consecutive_duplicate_within_5s_removed() -> None:
    segs = [
        {"text": "Thank you", "speaker": "Alice", "start": 10.0, "end": 11.0},
        {"text": "Thank you", "speaker": "Alice", "start": 12.0, "end": 13.0},
    ]
    result = filter_segments(segs)
    assert len(result) == 1
    assert result[0]["start"] == 10.0


def test_duplicate_beyond_5s_kept() -> None:
    segs = [
        {"text": "Thank you", "speaker": "Alice", "start": 0.0, "end": 1.0},
        {"text": "Thank you", "speaker": "Alice", "start": 11.0, "end": 12.0},
    ]
    assert len(filter_segments(segs)) == 2


def test_non_consecutive_same_text_kept() -> None:
    segs = [
        {"text": "Yes", "speaker": "Alice", "start": 0.0, "end": 1.0},
        {"text": "No", "speaker": "Bob", "start": 2.0, "end": 3.0},
        {"text": "Yes", "speaker": "Alice", "start": 4.0, "end": 5.0},
    ]
    assert len(filter_segments(segs)) == 3


# -- Valid segments pass through ----------------------------------------------


def test_mixed_segments() -> None:
    segs = [
        {"text": "Let's discuss the roadmap", "speaker": "Alice", "start": 0.0, "end": 4.2},
        {"text": "Sous-titrage ST' 501", "speaker": None, "start": 4.5, "end": 5.0},
        {"text": "Good point", "speaker": "Bob", "start": 5.5, "end": 7.0},
    ]
    result = filter_segments(segs)
    assert len(result) == 2
    assert result[0]["text"] == "Let's discuss the roadmap"
    assert result[1]["text"] == "Good point"
