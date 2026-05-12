# DHRS Hospital Server

Backend service for the **Dağıtık Hastane Randevu Sistemi (DHRS)** — a distributed hospital appointment and records system. Each participating hospital runs its own instance.

**Python 3.12 · FastAPI · PostgreSQL 16 · Redis · mTLS**

---

## What it does

- Creates and manages clinical sessions initiated by doctors
- Decrypts patient identity packages (forwarded by the App Server) and maps them to short-lived in-memory tokens
- Serves medical records to authorized doctors within active sessions
- Exchanges records with peer hospitals over mutual TLS, gated by a permissioned Trust Ledger
- Maintains two append-only, hash-chained audit ledgers: one for hospital membership events, one for all session and data events

Patient identity (MedID, personal info) is **never persisted to disk on this server** and **never included in any API response**.

---

## Quick start

### Tests (no external services required)

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### Full stack (Docker)

```bash
# 1. Create certs/
openssl genrsa -out certs/hospital_secret_key.pem 2048
openssl req -x509 -newkey rsa:2048 -keyout certs/server.key -out certs/server.crt \
  -days 365 -nodes -subj "/CN=hospital-alpha"
cp certs/server.crt certs/ca.crt

# 2. Create .env
cp .env.example .env
# Edit .env: set DATABASE_URL host → postgres, REDIS_URL host → redis
# Set KEK_HEX to 64 random hex chars (32 bytes)

# 3. Start
docker compose up --build
```

Server listens on `http://localhost:8000`. Health check: `GET /health`.

### Run tests in Docker

```bash
docker compose --profile test run --rm test
```

---

## Configuration

All settings are read from environment variables (or a `.env` file via pydantic-settings). Copy `.env.example` to get started.

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL async URL (`postgresql+asyncpg://…`) |
| `REDIS_URL` | Redis URL |
| `HSK_PEM_PATH` | Path to the hospital RSA-2048 private key PEM (used to decrypt patient info) |
| `KEK_HEX` | 64-char hex key (32 bytes) encrypting doctor private keys at rest |
| `HOSPITAL_ID` | This hospital's identifier string |
| `MTLS_CA_CERT_PATH` | CA certificate used to verify peer hospital mTLS connections |
| `SESSION_TTL_SECONDS` | Redis session TTL (default 3600) |

---

## Database migrations

```bash
alembic upgrade head
```

Requires a running PostgreSQL instance. The migration creates all tables and grants the app role `SELECT, INSERT` only on the ledger tables (no `UPDATE` or `DELETE`).

---

## API overview

| Caller | Endpoint | Purpose |
|---|---|---|
| App Server | `POST /sessions/authorize` | Deliver encrypted patient package, activate session |
| App Server | `POST /appointments/notify` | Register a booked appointment |
| Doctor terminal | `POST /sessions` | Create session, get QR payload |
| Doctor terminal | `GET /sessions/{id}/data` | Retrieve patient info + records (TempID in `X-Temp-Id` header) |
| Doctor terminal | `POST /sessions/{id}/records` | Write a new clinical record |
| Doctor terminal | `DELETE /sessions/{id}` | Terminate session |
| Doctor terminal | `POST /sessions/{id}/cross-hospital` | Request records from peer hospitals |
| Patient app | `POST /sessions/{id}/revoke` | Patient-initiated termination |
| Patient app | `GET /access-log` | Full audit trail |
| Peer hospital | `POST /cross-hospital/data` | Serve records to verified peer |
| Peer hospital | `POST /trust/membership` | Network join / vote |

All endpoints (except `POST /sessions/authorize` and `POST /appointments/notify`) require an `ACTIVE` session in Redis and a valid mTLS certificate passed via the `X-Client-Cert` header by the reverse proxy.

---

## Architecture notes

- **No patient data at rest** — the `TempID → MedID` mapping lives only in an in-memory table, wiped on session termination or server restart.
- **Atomic writes** — medical record insert and ledger entry are committed in the same DB transaction.
- **Hash chain integrity** — the Records Ledger uses `SELECT FOR UPDATE NOWAIT` to serialize appends; any tampered block breaks the chain.
- All internal modules are lazy singletons injected via FastAPI `Depends()`. See `app/modules/__init__.py`.
