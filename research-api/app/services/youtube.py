"""
YouTube transcript extraction using youtube-transcript-api.
"""
import logging
import re

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

logger = logging.getLogger(__name__)

_YT_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
]


def extract_video_id(url: str) -> str | None:
    for pattern in _YT_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript(url: str) -> str:
    """
    Fetch YouTube transcript as plain text.
    Raises ValueError with a user-facing Dutch message if transcript unavailable.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Geen geldige YouTube-URL herkend")

    try:
        # Prefer Dutch, fall back to English, then auto-generated
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_transcript(["nl", "en"])
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(["nl", "en"])

        entries = transcript.fetch()
        return " ".join(entry["text"] for entry in entries)

    except TranscriptsDisabled:
        raise ValueError("Transcripts zijn uitgeschakeld voor deze video")
    except NoTranscriptFound:
        raise ValueError("Geen transcript beschikbaar voor deze video")
    except Exception as exc:
        logger.exception("YouTube transcript fetch failed for %s", url)
        raise ValueError(f"Transcript ophalen mislukt: {exc}") from exc
