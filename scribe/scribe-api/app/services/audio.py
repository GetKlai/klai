"""
Audio preprocessing for scribe-api.

Accepts raw upload bytes, validates format with python-magic,
and converts to WAV 16 kHz mono int16 PCM using pydub + ffmpeg.
Audio never touches disk outside the request lifecycle.
"""
import io
import logging

import magic
from fastapi import HTTPException, status
from pydub import AudioSegment

logger = logging.getLogger(__name__)

ALLOWED_MIME_PREFIXES = (
    "audio/",
    "video/webm",
    "video/ogg",
    "video/mp4",
)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".mp4"}


def _check_mime(data: bytes) -> str:
    mime = magic.from_buffer(data[:2048], mime=True)
    if not any(mime.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bestandsformaat niet ondersteund: {mime}",
        )
    return mime


def normalize_audio(data: bytes, filename: str) -> bytes:
    """
    Convert arbitrary audio/video bytes to WAV 16 kHz mono int16 PCM.
    Returns the normalized WAV as bytes.
    """
    _check_mime(data)

    try:
        segment = AudioSegment.from_file(io.BytesIO(data))
    except Exception as exc:
        logger.warning("pydub failed to decode audio: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio kon niet worden gelezen. Controleer het bestandsformaat.",
        ) from exc

    segment = segment.set_frame_rate(16000).set_channels(1).set_sample_width(2)

    buf = io.BytesIO()
    segment.export(buf, format="wav")
    buf.seek(0)
    return buf.read()
