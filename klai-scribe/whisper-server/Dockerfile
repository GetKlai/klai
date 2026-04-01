FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
ENV UV_BREAK_SYSTEM_PACKAGES=1
RUN pip install --no-cache-dir --break-system-packages uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY logging_setup.py main.py ./

ENV WHISPER_MODEL=large-v3-turbo
ENV WHISPER_DEVICE=cuda
ENV WHISPER_COMPUTE_TYPE=float16
ENV WHISPER_DOWNLOAD_ROOT=/models
ENV LOG_LEVEL=INFO

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8000

USER klai

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
