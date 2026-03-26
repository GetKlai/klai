FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    FlagEmbedding \
    torch \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY main.py .

ENV WHISPER_DOWNLOAD_ROOT=/models
ENV MODEL_NAME=BAAI/bge-m3
ENV HF_HOME=/models

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
