FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Production image ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS production
WORKDIR /app

RUN addgroup --gid 1000 dhrs && adduser --uid 1000 --gid 1000 --no-create-home --disabled-password dhrs

COPY --from=builder /install /usr/local
COPY app/ ./app/

USER dhrs
EXPOSE 8000

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ── Test image ────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS test
WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/
COPY tests/ ./tests/
COPY pytest.ini .

CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
