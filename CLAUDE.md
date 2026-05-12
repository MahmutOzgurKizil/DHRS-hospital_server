# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

The Hospital Server is the core backend service for the DHRS (Dağıtık Hastane Randevu Sistemi) system. It manages clinical sessions, decrypts and temporarily maps patient identifiers, serves medical records, coordinates cross-hospital mTLS data exchange, and maintains a dual append-only ledger system.

The server **never communicates directly with the patient's mobile app** for session data — all session packages originate from the App Server (identity broker).

---

## Commands

```bash
# Run all tests (no external services needed — SQLite + fakeredis)
pytest tests/ -v

# Run a single test file
pytest tests/test_sessions.py -v

# Run a single test by name
pytest tests/test_sessions.py::test_authorize_session -v

# Run tests in Docker (matches CI)
docker compose --profile test run --rm test

# Start full production stack (postgres + redis + app)
docker compose up --build

# Run migrations (requires running PostgreSQL)
alembic upgrade head
```

Local test runs require env vars — `conftest.py` sets them automatically before any app import, so `pytest` works without a `.env` file.

For `docker compose up --build`, copy `.env.example` to `.env`, update `DATABASE_URL` and `REDIS_URL` to use Docker service names (`postgres`/`redis`), and populate `certs/` with a generated RSA key (`HSK_PEM_PATH`) and CA cert (`MTLS_CA_CERT_PATH`).

---

## Architecture

```
Incoming traffic → API Gateway (verify_mtls + require_active_session) → Router → Module(s) → Storage
```

All internal modules are **lazy singletons** in `app/modules/__init__.py`, injected via `Depends()`. `AsyncSession` and `Redis` are injected per-request — modules never store them as instance state.

| Module | Responsibility |
|---|---|
| `DecryptionEngine` | Decrypts `enc_pinfo` (HSK/RSA-OAEP hybrid) and `enc_med_id` (doctor DSK/RSA-OAEP) |
| `SessionMappingModule` | Generates TempID, writes `TempID → MedID` to the in-memory Medical ID Table |
| `DataRetrievalModule` | Resolves `TempID → MedID`, queries Medical DB, delegates to CrossHospitalModule |
| `DataWriteModule` | Inserts medical record + Records Ledger entry in one atomic transaction |
| `CrossHospitalModule` | Verifies Trust Ledger for peer, fetches records over mTLS |
| `LedgerInterfaceModule` | Sole writer to both ledgers |
| `SessionTerminationModule` | Wipe table → delete Redis → append ledger (strict order) |
| `AppointmentModule` | Appointment registration and desk lookup |

### Storage layers

- **PostgreSQL**: `medical_records`, `doctor_keys`, `trust_ledger_blocks`, `records_ledger_entries`, `appointments` — schema in `alembic/versions/001_initial_schema.py` and `app/models/`
- **Redis**: `session:{session_id}` JSON with TTL; statuses `PENDING → ACTIVE → (deleted)`
- **Medical ID Table** (`app/storage/medical_id_table.py`): in-memory only, `asyncio.Lock`-protected, module-level singleton; holds `TempID → MedID + pinfo + cross_hospital_records`

### Session lifecycle

```
POST /sessions          → Redis: PENDING, ledger: ACCESS_REQUEST
POST /sessions/authorize → decrypt enc_pinfo + enc_med_id → TempID → Redis: ACTIVE, ledger: APPROVAL + SESSION_OPEN
GET  /sessions/{id}/data → resolve TempID → MedID → query DB
POST /sessions/{id}/records → medical INSERT + ledger INSERT (atomic)
DELETE /sessions/{id}   → wipe table → delete Redis key → ledger: SESSION_CLOSE
```

---

## Key Constraints

**FastAPI 204 endpoints**: FastAPI 0.115 raises `AssertionError` when a route has `status_code=204` in the decorator with any inferred response model. Always use `-> Response` and `return Response(status_code=204)` — never `-> None` with `status_code=204`.

**SQLite test compatibility**: `app/models/base.py` uses `JSONB.with_variant(JSON(), "sqlite")`. `app/storage/database.py` skips `pool_size`/`max_overflow` kwargs when the URL starts with `sqlite`.

**Hash chain atomicity**: `append_records_entry` does `SELECT … FOR UPDATE NOWAIT` (PostgreSQL only; skipped on SQLite). The exact Python `datetime.now(tz=utc).isoformat()` string is stored as `ledger_timestamp` and used for `block_hash` computation — do **not** use `created_at.isoformat()`, which is set by the DB server clock and will differ.

**Dependency overrides in tests**: `conftest.py` overrides `get_db` → SQLite session, `get_redis` → fakeredis, `verify_mtls` → returns `"test-hospital"` string. Module singletons (`get_ledger`, `get_retrieval`, etc.) are also overridable the same way.

---

## Security Invariants

1. `MedID` never appears in any response, log, or DB column outside `medical_records.med_id`. No response schema has a `med_id` field.
2. `TempID` is never written to any storage layer — only the Medical ID Table (in-memory).
3. Doctor DSK `bytearray` is zero-filled in `finally` inside `decrypt_med_id` regardless of success/failure.
4. Cross-hospital records are never inserted into the local DB — stored in `MedicalIDEntry.cross_hospital_records` for session duration only.
5. Records Ledger is append-only; DB role has `SELECT, INSERT` only — no `UPDATE/DELETE`.
6. Trust Ledger verification (`verify_trust_ledger_for_peer`) is mandatory before any cross-hospital call — mTLS cert alone is insufficient.
7. Termination order is strict: `table.delete` → `delete_session` → ledger append. A ledger failure must not leave sensitive data in memory.

---

## Crypto Reference

| Operation | Mechanism |
|---|---|
| `enc_pinfo` decryption | `[2B key_len][RSA-OAEP(HSK, aes_key)][12B nonce][AES-256-GCM(aes_key, pinfo_json)]` |
| `enc_med_id` decryption | RSA-OAEP with doctor private key (DSK) |
| Doctor DSK at rest | `[12B nonce][AES-256-GCM(KEK, dsk_pem)]` |
| KEK | `KEK_HEX` env var (64 hex chars = 32 bytes); `settings.kek_bytes` property |
| Records Ledger `block_hash` | SHA-256 of `sort_keys=True` JSON `{event_type, hashes…, timestamp, prev_hash}` |
| Trust Ledger `block_hash` | SHA-256 of `sort_keys=True` JSON `{block_index, event_type, subject_hospital, subject_pubkey, approved_by (sorted), timestamp, prev_hash}` |

HSK = RSA-2048 private key at `HSK_PEM_PATH`; loaded via `@cached_property` in `DecryptionEngine`.
