FROM python:3.11-slim

# SEC-018 F-029: create non-root user + writable model cache before runtime.
RUN useradd --create-home --uid 1000 --shell /bin/bash app && \
    mkdir -p /models && chown -R app:app /models

WORKDIR /app

# Install CUDA-enabled PyTorch (cu128 = CUDA 12.8, matches gpu-01 driver 570.x exactly).
# cu121 without version cap installs torch 2.11.0 which pulls CUDA 13.x runtime (requires driver ≥572).
# cu128 installs torch 2.11.0 with CUDA 12.8 runtime — native match for driver 570.x.
# The CPU-only wheel was previously used here, but BGE-M3 on CPU takes >5s per
# realistic batch (1500-char chunks), causing ReadTimeout in knowledge-ingest.
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    "FlagEmbedding>=1.3.0" \
    "transformers<4.52" \
    torch \
    --extra-index-url https://download.pytorch.org/whl/cu128

COPY --chown=app:app main.py .

ENV WHISPER_DOWNLOAD_ROOT=/models
ENV MODEL_NAME=BAAI/bge-m3
ENV HF_HOME=/models

# SEC-018: drop root for runtime — BGE-M3 writes model weights into /models
# which is owned by the `app` user at build time.
USER app

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
