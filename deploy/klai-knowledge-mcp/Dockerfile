FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY logging_setup.py .
COPY main.py .

EXPOSE 8080

CMD ["python", "main.py"]
