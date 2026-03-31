FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY logging_setup.py .
COPY main.py .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8080

USER klai

CMD ["python", "main.py"]
