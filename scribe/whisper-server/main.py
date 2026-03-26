"""
whisper-server: thin FastAPI wrapper around faster-whisper.

Endpoints:
  POST /v1/audio/transcriptions  — OpenAI-compatible multipart upload
  GET  /health                   — readiness + queue depth
"""
import asyncio
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "/models")

# faster-whisper is not thread-safe for inference — serialize with a lock
_inference_lock = asyncio.Lock()
_queue_depth = 0

model: WhisperModel


def _load_model() -> WhisperModel:
    logger.info(
        "Loading whisper model %s on %s (%s)",
        WHISPER_MODEL,
        WHISPER_DEVICE,
        WHISPER_COMPUTE_TYPE,
    )
    return WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        download_root=WHISPER_DOWNLOAD_ROOT,
    )


def _warmup(m: WhisperModel) -> None:
    """Transcribe 1 second of silence to trigger model compilation and load weights."""
    logger.info("Warming up model...")
    import numpy as np
    import soundfile as sf

    silence = np.zeros(16000, dtype=np.float32)
    tmp = Path(tempfile.mktemp(suffix=".wav"))
    try:
        sf.write(str(tmp), silence, 16000)
        segs, _ = m.transcribe(str(tmp), language="nl")
        list(segs)  # consume generator to trigger full compilation
    except Exception as exc:
        logger.warning("Warmup failed (non-fatal): %s", exc)
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("Model ready.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global model
    loop = asyncio.get_event_loop()
    model = await loop.run_in_executor(None, _load_model)
    await loop.run_in_executor(None, _warmup, model)
    yield


app = FastAPI(title="whisper-server", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> JSONResponse:
    global _queue_depth
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Empty audio file")

    _queue_depth += 1
    try:
        async with _inference_lock:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _run_transcription, audio_bytes, language
            )
    finally:
        _queue_depth -= 1

    return JSONResponse(result)


def _run_transcription(audio_bytes: bytes, language: str | None) -> dict:
    """Blocking transcription — runs in a thread executor."""
    tmp_path = Path(tempfile.gettempdir()) / f"scribe_{uuid.uuid4().hex}.wav"
    try:
        tmp_path.write_bytes(audio_bytes)
        t0 = time.monotonic()
        segments, info = model.transcribe(
            str(tmp_path),
            language=language or None,
            beam_size=5,
        )
        # Consume the generator into a list before building output
        segments_list = list(segments)
        elapsed = time.monotonic() - t0
        text_parts = [seg.text for seg in segments_list]
        full_text = " ".join(text_parts).strip()
        return {
            "text": full_text,
            "language": info.language,
            "duration": info.duration,
            "inference_time_seconds": round(elapsed, 3),
            "model": WHISPER_MODEL,
            "segments": [
                {"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()}
                for seg in segments_list
            ],
        }
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": WHISPER_MODEL,
        "device": WHISPER_DEVICE,
        "queue_depth": _queue_depth,
    }
