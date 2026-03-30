FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY app/ ./app/
COPY theme/ ./theme/

RUN useradd --system --no-create-home mailer
USER mailer

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
