FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY . .

ENV LOG_LEVEL=INFO

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8030

USER klai

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]
