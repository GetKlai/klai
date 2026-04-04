FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY . .

ENV LOG_LEVEL=INFO

RUN adduser --disabled-password --gecos "" klai && \
    mkdir -p /data/audio && chown klai:klai /data/audio

EXPOSE 8020

USER klai

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
