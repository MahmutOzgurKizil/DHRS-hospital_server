# Hospital Server — CLAUDE.md

## Overview

The Hospital Server is the core backend service operated by each participating hospital in the
DHRS (Dağıtık Hastane Randevu Sistemi) system. It sits behind an API Gateway and is
responsible for:

- Managing clinical sessions (create, authorize, terminate)
- Decrypting and temporarily mapping patient identifiers
- Serving medical records to authorized doctors
- Coordinating cross-hospital data exchange over mTLS
- Maintaining cryptographically verifiable audit trails via a dual-ledger system

The Hospital Server never communicates directly with the patient's mobile application for
session data. All session packages originate from the App Server, which acts as the identity
broker.

---

## Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Framework | FastAPI 0.115.5 (async throughout) |
| ORM / DB driver | SQLAlchemy 2.0 async + asyncpg (PostgreSQL 16) |
| Session store | redis-py async + hiredis |
| Crypto | `cryptography` 43 — RSA-OAEP, AES-256-GCM |
| HTTP client | httpx (HTTP/2, mTLS) |
| Migrations | Alembic 1.14 |
| Schemas | Pydantic v2 |
| Tests | pytest + pytest-asyncio (mode=auto) + fakeredis + aiosqlite |

---

## Architecture

The server is composed of **specialized internal modules** accessed exclusively through a
central API Gateway. No internal module is directly reachable from the outside network.

```
Incoming traffic → API Gateway → Internal Module(s) → Storage
```

### Internal Modules

| Module | Responsibility |
|---|---|
| **DecryptionEngine** | Decrypts `enc_pinfo` (via HSK) and `enc_med_id` (via doctor's private key) on session authorize |
| **SessionMappingModule** | Generates `TempID`, writes `TempID → MedID` to the in-memory Medical ID Table |
| **DataRetrievalModule** | Resolves `TempID → MedID`, queries Medical Database, optionally delegates to Cross-Hospital Module |
| **DataWriteModule** | Validates session, resolves MedID, appends record to Medical DB, commits content hash to Records Ledger (atomic) |
| **CrossHospitalModule** | Queries Trust Ledger for peer hospitals, fetches external record indexes and full records over mTLS |
| **LedgerInterfaceModule** | Sole writer to both distributed ledgers (Trust Ledger queries + Records Ledger writes) |
| **SessionTerminationModule** | Clears Redis state, wipes Medical ID Table entry, discards in-memory aggregated records, logs `SESSION_CLOSE` |
| **AppointmentModule** | Receives appointment notifications from App Server; supports registration desk lookup |

All modules are lazy singletons in `app/modules/__init__.py` and injected via `Depends()`.
`AsyncSession` and `Redis` are injected per-request; modules never store them as instance state.

---

## File Structure

```
requirements.txt
.env.example
Dockerfile                    # multi-stage: builder → production / test
docker-compose.yml            # postgres:16-alpine, redis:7-alpine, app, test (profile)
pytest.ini

app/
  config.py                   # Settings(BaseSettings) — all fields have defaults
  main.py                     # FastAPI app, lifespan, router registration

  storage/
    database.py               # async engine + get_db dependency
    redis_store.py            # get_redis + session helpers
    medical_id_table.py       # in-memory MedicalIDTable + module-level singleton

  models/
    base.py                   # DeclarativeBase, JSONB, new_uuid
    medical_record.py
    doctor_key.py
    trust_block.py
    records_ledger.py
    appointment.py

  schemas/
    session.py
    record.py
    appointment.py
    trust.py
    cross_hospital.py

  modules/
    __init__.py               # lazy singleton providers (get_ledger, get_termination, …)
    decryption_engine.py
    session_mapping.py
    data_retrieval.py
    data_write.py
    cross_hospital.py
    ledger_interface.py
    session_termination.py
    appointment_module.py

  middleware/
    mtls.py                   # verify_mtls FastAPI dependency
    session_auth.py           # require_active_session FastAPI dependency

  routers/
    sessions.py
    appointments.py
    cross_hospital.py
    trust.py
    access_log.py

alembic/
  env.py
  versions/001_initial_schema.py

tests/
  conftest.py
  test_decryption_engine.py
  test_ledger_interface.py
  test_medical_id_table.py
  test_session_termination.py
  test_sessions.py
```

---

## Storage Components

### Medical Database (`medical_records`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `med_id` | UUID | indexed; never in any response |
| `record_type` | VARCHAR(50) | |
| `content` | JSONB | |
| `content_hash` | CHAR(64) | SHA-256 of content, committed to Records Ledger |
| `doctor_id` | VARCHAR(100) | |
| `created_at` | TIMESTAMPTZ | server default NOW() |

No FK to any identity table. `INSERT` and `SELECT` only.

### Doctor Key Table (`doctor_keys`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `doctor_id` | VARCHAR(100) UNIQUE | |
| `doctor_name` | VARCHAR(200) | |
| `encrypted_dsk` | BYTEA | `[12B nonce][AES-256-GCM(KEK, doctor_private_key_pem)]` |
| `public_key_pem` | TEXT | |
| `is_active` | BOOLEAN | key rotation: set old row to FALSE, insert new |
| `created_at` | TIMESTAMPTZ | |

Never exposed through the API Gateway. Accessed only by `DecryptionEngine`.

### Redis Session Store
- Key: `session:{session_id}` (JSON string) with native TTL
- Statuses: `PENDING → ACTIVE → (key deleted on termination)`
- `require_active_session` dependency asserts `status == "ACTIVE"` before any session-scoped route

### Medical ID Table (`app/storage/medical_id_table.py`)
```python
@dataclass
class MedicalIDEntry:
    temp_id: str
    med_id: str                           # NEVER logged, never persisted
    pinfo: dict[str, Any]                 # decrypted patient info
    cross_hospital_records: dict[str, Any]
    expires_at: datetime
```
- **In-memory only** — module-level singleton `medical_id_table = MedicalIDTable()`
- Protected by `asyncio.Lock` for concurrent access safety
- `delete()` zero-fills all sensitive fields (`med_id`, `pinfo`, `cross_hospital_records`, `temp_id`) before `del`
- `purge_expired()` called every 60 s by background task in `app/main.py`

### Trust Ledger (`trust_ledger_blocks`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `block_index` | INTEGER UNIQUE | |
| `event_type` | VARCHAR(10) | `JOIN \| LEAVE \| REVOKE` |
| `subject_hospital` | CHAR(64) | SHA-256 of hospital_id |
| `subject_pubkey` | TEXT nullable | HPK PEM on JOIN |
| `approved_by` | JSONB | list of SHA-256(hospital_id) |
| `block_hash` | CHAR(64) | |
| `prev_block_hash` | CHAR(64) | |
| `ledger_timestamp` | VARCHAR(50) | exact isoformat string used in block_hash computation |
| `created_at` | TIMESTAMPTZ | |

### Records Ledger (`records_ledger_entries`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `block_index` | BIGINT UNIQUE | manually assigned under `SELECT FOR UPDATE NOWAIT` |
| `event_type` | VARCHAR(30) | see event types below |
| `session_id_hash` | CHAR(64) | |
| `patient_id_hash` | CHAR(64) nullable | |
| `doctor_id_hash` | CHAR(64) nullable | |
| `hospital_id_hash` | CHAR(64) nullable | |
| `content_hash` | CHAR(64) nullable | only on DATA_WRITE |
| `prev_hash` | CHAR(64) | |
| `block_hash` | CHAR(64) | |
| `ledger_timestamp` | VARCHAR(50) | exact isoformat string used in block_hash computation |
| `created_at` | TIMESTAMPTZ | |

App DB role has `SELECT, INSERT` only — no `UPDATE` or `DELETE` (enforced in migration).

---

## Session Lifecycle

```
Doctor selects appointment
  → POST /sessions                        (Hospital Server creates session, status: PENDING)
  → QR displayed on terminal
  → Patient scans QR via mobile app
  → App Server forwards POST /sessions/authorize
  → DecryptionEngine decrypts enc_pinfo + enc_med_id
  → SessionMappingModule generates TempID, writes to Medical ID Table
  → Redis status → ACTIVE
  → Doctor terminal transitions to data view (GET /sessions/{id}/data)
  → [Optional] Doctor writes records (POST /sessions/{id}/records)
  → [Optional] Doctor requests cross-hospital data (POST /sessions/{id}/cross-hospital)
  → Session ends via: doctor DELETE, patient POST /revoke, Redis TTL, or inactivity timeout
  → SessionTerminationModule: wipe table → delete Redis → append SESSION_CLOSE to ledger
```

Session state machine is authoritative in Redis. Medical ID Table entries mirror Redis TTL.

---

## Crypto Design

| Operation | Mechanism |
|---|---|
| `enc_pinfo` decryption | Hybrid: `[2B key_len][RSA-OAEP(HSK, aes_key)][12B nonce][AES-256-GCM(aes_key, pinfo_json)]` |
| `enc_med_id` decryption | RSA-OAEP with doctor's private key (DSK) |
| Doctor DSK at rest | `[12B nonce][AES-256-GCM(KEK, doctor_private_key_pem)]` |
| KEK source | `KEK_HEX` env var (exactly 64 hex chars = 32 bytes); accessed via `settings.kek_bytes` property |
| Records Ledger `block_hash` | SHA-256 of `sort_keys=True` JSON: `{event_type, hashes…, timestamp, prev_hash}` |
| Trust Ledger `block_hash` | SHA-256 of `sort_keys=True` JSON: `{block_index, event_type, subject_hospital, subject_pubkey, approved_by (sorted), timestamp, prev_hash}` |

**HSK** (`hospital_secret_key.pem`) — RSA-2048 private key; loaded lazily via `@cached_property` in
`DecryptionEngine._hsk`. The corresponding public key (HPK) is distributed to other hospitals
and used to encrypt `enc_pinfo`.

Doctor DSK `bytearray` is zero-filled in a `finally` block immediately after `decrypt_med_id`
regardless of success or failure.

---

## Security Invariants

These must be upheld at all times:

1. **`MedID` never leaves the Hospital Server in plaintext.** It exists in memory only during
   the `DecryptionEngine` handoff to `SessionMappingModule`, and within the Medical ID Table
   (in-memory, never logged). No response schema contains a `med_id` field.

2. **`TempID` is never forwarded to any storage layer.** Only the Medical ID Table holds
   the `TempID → MedID` mapping; all DB queries use `MedID`.

3. **Doctor private keys are decrypted in-process and discarded immediately after use.**
   `dsk_pem` is a `bytearray`; zeroed in `finally` before `decrypt_med_id` returns.

4. **Cross-hospital records are never written to the local Medical Database.** They exist
   in `MedicalIDEntry.cross_hospital_records` for the duration of the session only.

5. **The Records Ledger is append-only.** Any modification breaks the hash chain and is
   detectable. DB role has no `UPDATE` or `DELETE` privilege on ledger tables.

6. **Trust Ledger verification is mandatory before any cross-hospital data exchange.**
   `CrossHospitalModule.request_record_index` calls `ledger.verify_trust_ledger_for_peer()`
   before building the httpx client. mTLS cert validity alone is not sufficient.

7. **Session Termination clears memory before writing to the ledger.** Fixed order in
   `SessionTerminationModule.terminate`: `table.delete` → `delete_session` → ledger append.
   Ledger failure does not leave sensitive data resident in memory.

---

## Hash Chain Atomicity (Records Ledger)

`LedgerInterfaceModule.append_records_entry` is always called within an open SQLAlchemy
transaction (caller owns commit/rollback):

1. `SELECT block_hash, block_index … ORDER BY block_index DESC LIMIT 1 FOR UPDATE NOWAIT`
   (PostgreSQL only — SQLite skips the lock for test compatibility)
2. Compute `block_hash` using `prev_hash` and store the exact `datetime.now(tz=utc).isoformat()`
   string as `ledger_timestamp`
3. `db.add(entry)` + `await db.flush()`
4. Caller does `await db.commit()`

`FOR UPDATE NOWAIT` causes concurrent appenders to immediately raise `LockNotAvailable`
(→ 503 retryable), guaranteeing no two transactions share the same `prev_hash`.

`DataWriteModule` performs medical record INSERT + ledger INSERT in the same transaction → atomic.

**Critical**: `verify_trust_ledger_for_peer` replays the hash chain using `block.ledger_timestamp`
(the stored string), not `block.created_at.isoformat()`. These differ because `created_at`
is set by the DB server default (`NOW()`), not the Python timestamp used during hashing.

---

## API Gateway Rules

All inbound connections must:
- Present a valid mTLS certificate from a trusted CA
- Carry a valid session token (except `POST /sessions/authorize`)
- Reference an `ACTIVE` session in Redis (except session creation endpoints)

`verify_mtls` reads the `X-Client-Cert` header (populated by the reverse proxy after TLS
termination), validates against `MTLS_CA_CERT_PATH`, and returns the `hospital_id` extracted
from the certificate's CN field.

`require_active_session` reads `session_id` from the URL path parameter, fetches the session
from Redis, and asserts `status == "ACTIVE"`.

The Gateway holds **no business logic, no patient data, no keys, no session state**.

---

## API Surface (summary)

### Called by App Server
| Endpoint | Purpose |
|---|---|
| `POST /sessions/authorize` | Deliver encrypted PINFO + MedID, trigger decryption and session activation |
| `POST /appointments/notify` | Register a booked appointment (no patient identity) |

### Called by Doctor's Terminal
| Endpoint | Purpose |
|---|---|
| `POST /sessions` | Initiate session for appointment, returns QR payload |
| `GET /sessions/{id}/data` | Retrieve patient PINFO + local medical records |
| `POST /sessions/{id}/records` | Submit new clinical record |
| `DELETE /sessions/{id}` | Doctor-initiated session termination |
| `POST /sessions/{id}/cross-hospital` | Request external record index from peer hospitals |
| `GET /sessions/{id}/cross-hospital/{record_id}` | Fetch a single external record |
| `GET /appointments/register` | Registration desk lookup |

### Called by Patient Mobile App
| Endpoint | Purpose |
|---|---|
| `POST /sessions/{id}/revoke` | Patient-initiated session termination |
| `POST /sessions/{id}/consent` | Grant/revoke consent for cross-hospital sharing |
| `GET /access-log` | Retrieve full session event history (hashes resolved to display names) |

### Called by Peer Hospitals
| Endpoint | Purpose |
|---|---|
| `POST /cross-hospital/data` | Serve records to a verified peer hospital |
| `POST /trust/membership` | Apply to join network or cast a membership vote |

`TempID` is passed in the `X-Temp-Id` **header** on `GET /sessions/{id}/data`, never in the URL,
to prevent it from appearing in server access logs.

---

## Dual-Ledger System

### Trust Ledger (hospital membership)
- Permissioned blockchain determining which hospitals are active network members
- New member admission requires a majority vote from existing members
- Verification: scan all blocks in order → replay hash chain → find most recent block for peer
  → confirm `event_type == JOIN` → confirm `subject_pubkey` matches presented HPK

### Records Ledger (audit trail)
- Append-only log of all session and data events
- Stores only hashes, event types, content hashes, timestamps — no plaintext data
- Event types: `ACCESS_REQUEST | SESSION_OPEN | APPROVAL | REJECTION | DATA_WRITE |
  SESSION_CLOSE | CROSS_HOSPITAL_REQUEST | PATIENT_REVOCATION`
- Cross-hospital exchanges documented on **both** hospitals' ledgers with the same
  `session_id_hash` and `patient_id_hash`

---

## Running

```bash
# Tests (no external services — SQLite + fakeredis in Docker)
docker compose --profile test run --rm test

# Production stack
docker compose up --build

# Migrations (requires running PostgreSQL)
alembic upgrade head
```

`conftest.py` sets env vars at module level before any app import so pydantic-settings
picks them up correctly. Test fixtures: `db_session` (SQLite in-memory), `fake_redis`
(fakeredis), `test_table` (fresh `MedicalIDTable()`), `client` (`AsyncClient` + `ASGITransport`
with dependency overrides for `get_db`, `get_redis`, `verify_mtls`).

---

## Key Design Constraints

- The Personal Database (patient identity) lives on the **App Server**, not here.
- The App Server is **fully stateless after forwarding** — it does not track what happens
  inside a Hospital Server session.
- `DataWriteModule`: medical record INSERT + Records Ledger INSERT are in the same DB
  transaction. A failed ledger write rolls back the medical record insert entirely.
- FastAPI 204 endpoints must return `Response(status_code=204)` explicitly — FastAPI 0.115
  raises `AssertionError` if a route with `status_code=204` has any inferred response model.
  Never use `-> None` with `status_code=204`; use `-> Response` instead.
- SQLite compatibility for tests: `app/models/base.py` uses `JSONB.with_variant(JSON(), "sqlite")`;
  `app/storage/database.py` skips `pool_size`/`max_overflow` kwargs when `database_url`
  starts with `sqlite`.
