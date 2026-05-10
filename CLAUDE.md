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

## Architecture

The server is composed of **specialized internal modules** accessed exclusively through a
central API Gateway. No internal module is directly reachable from the outside network.

```
Incoming traffic → API Gateway → Internal Module(s) → Storage
```

### Internal Modules

| Module | Responsibility |
|---|---|
| **Decryption Engine** | Decrypts `enc_pinfo` (via HSK) and `enc_med_id` (via doctor's private key) on session authorize |
| **Session Mapping Module** | Generates `TempID`, writes `TempID → MedID` to the in-memory Medical ID Table |
| **Data Retrieval Module** | Resolves `TempID → MedID`, queries Medical Database, optionally delegates to Cross-Hospital Module |
| **Data Write Module** | Validates session, resolves MedID, appends record to Medical DB, commits content hash to Records Ledger (atomic) |
| **Cross-Hospital Module** | Queries Trust Ledger for peer hospitals, fetches external record indexes and full records over mTLS |
| **Ledger Interface Module** | Sole writer to both distributed ledgers (Trust Ledger queries + Records Ledger writes) |
| **Session Termination Module** | Clears Redis state, wipes Medical ID Table entry, discards in-memory aggregated records, logs `SESSION_CLOSE` |
| **Appointment Module** | Receives appointment notifications from App Server; supports registration desk lookup |

---

## Storage Components

### Medical Database
- Indexed exclusively by `med_id`
- **No join path to any identity database** — shares no keys with patient identity stores
- `INSERT` and `SELECT` only; cross-hospital records are never written here (memory only)
- Every write produces a `content_hash` (SHA-256) committed to the Records Ledger

### Doctor Private Key Table
- Maps `doctor_id` to encrypted private key (`DSK`, encrypted at rest under `HSK`)
- **Never exposed through the API Gateway**
- Accessible only by the Decryption Engine via direct internal call
- Key rotation: old rows kept for audit, `is_active = FALSE`; new row inserted

### Redis Session Store
- Key: `session:{session_id}` with native TTL
- Statuses: `PENDING → ACCEPTED → ACTIVE → TERMINATED`
- TTL expiry auto-terminates sessions regardless of application state

### Medical ID Table
- **In-memory only** — `HashMap<session_id, {temp_id, med_id, expires_at}>`
- Never written to disk, never logged, never replicated
- Server crash = instant wipe = all active sessions terminate

### Trust Ledger (local copy)
- Read for membership verification before any cross-hospital operation
- Written to only by Ledger Interface Module when propagating new blocks
- Validated on every block receipt: recompute `block_hash`, verify `prev_block_hash`

### Records Ledger
- Append-only audit log for all session and data events
- Stores only hashes, event types, and timestamps — **no plaintext medical or identity data**
- `INSERT` privilege held only by the Ledger Interface Module

---

## Session Lifecycle

```
Doctor selects appointment
  → POST /sessions                        (Hospital Server creates session, status: PENDING)
  → QR displayed on terminal
  → Patient scans QR via mobile app
  → App Server forwards POST /sessions/authorize
  → Decryption Engine decrypts enc_pinfo + enc_med_id
  → Session Mapping Module generates TempID, writes to Medical ID Table
  → Status: ACTIVE
  → Doctor terminal transitions to data view (GET /sessions/{id}/data)
  → [Optional] Doctor writes records (POST /sessions/{id}/records)
  → [Optional] Doctor requests cross-hospital data (POST /sessions/{id}/cross-hospital)
  → Session ends via: doctor DELETE, patient POST /revoke, Redis TTL, or inactivity timeout
  → Session Termination Module clears all state + logs SESSION_CLOSE
```

Session state machine is authoritative in Redis. Medical ID Table entries mirror Redis TTL.

---

## Security Invariants

These must be upheld at all times:

1. **`MedID` never leaves the Hospital Server in plaintext.** It exists in memory only during
   the Decryption Engine handoff to Session Mapping Module, and within the Medical ID Table
   (in-memory, never logged).

2. **`TempID` is never forwarded to any storage layer.** Only the Medical ID Table holds
   the `TempID → MedID` mapping; all DB queries use `MedID`.

3. **Doctor private keys are decrypted in-process and discarded immediately after use.**
   One key, one session, then wiped from memory.

4. **Cross-hospital records are never written to the local Medical Database.** They exist
   in memory for the duration of the session only and are discarded on termination.

5. **The Records Ledger is append-only.** Any modification breaks the hash chain and is
   detectable.

6. **Trust Ledger verification is mandatory before any cross-hospital data exchange.**
   mTLS certificate validity alone is not sufficient; the ledger must confirm the peer hospital
   has an active `JOIN` event and an unbroken hash chain.

7. **Session Termination clears memory before writing to the ledger.** A ledger write
   failure must not leave sensitive data resident in memory.

---

## API Gateway Rules

All inbound connections must:
- Present a valid mTLS certificate from a trusted CA
- Carry a valid session token (except `POST /sessions/authorize`)
- Reference an `ACTIVE` session in Redis (except session creation endpoints)

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
| `GET /access-log` | Retrieve full session event history (hashes resolved to display names) |

### Called by Peer Hospitals
| Endpoint | Purpose |
|---|---|
| `POST /cross-hospital/data` | Serve records to a verified peer hospital |
| `POST /trust/membership` | Apply to join network or cast a membership vote |

Full request/response schemas are in Section 4.6 of the design document.

---

## Dual-Ledger System

### Trust Ledger (hospital membership)
- Permissioned blockchain determining which hospitals are active network members
- New member admission requires a majority vote from existing members
- Block fields: `event_type` (`JOIN | LEAVE | REVOKE`), `subject_hospital` (SHA-256),
  `subject_pubkey` (HPK, JOIN only), `approved_by[]`, hash chain fields
- Verification: find most recent block for peer → confirm `event_type == JOIN` →
  verify unbroken hash chain → confirm HPK matches mTLS cert presented

### Records Ledger (audit trail)
- Append-only log of all session and data events
- Stores: hashed identifiers, event type, content hashes (DATA_WRITE), timestamps, prev hash
- Event types: `ACCESS_REQUEST | SESSION_OPEN | APPROVAL | REJECTION | DATA_WRITE |
  SESSION_CLOSE | CROSS_HOSPITAL_REQUEST | PATIENT_REVOCATION`
- Cross-hospital exchanges are independently documented on **both** hospitals' ledgers with
  the same `session_id` and `patient_id_hash` — neither side can deny participation

---

## Key Design Constraints

- The Personal Database (patient identity) lives on the **App Server**, not here. The Hospital
  Server has no access to it and cannot join against it.
- The App Server is **fully stateless after forwarding** — it does not track what happens inside
  a Hospital Server session.
- The `GET /sessions/{id}/data` endpoint passes `TempID` in the `X-Temp-Id` **header**,
  not the URL, to prevent it appearing in server access logs.
- A record written to the Medical Database whose hash fails to reach the Records Ledger is
  flagged as incomplete and queued for resolution (Steps 4+5 of Data Write are treated as
  atomic).
- The `POST /sessions/{id}/consent` endpoint exists for cross-hospital flows that require
  patient approval (see Figure 17).

---

## References

- **Figure 12** — Hospital Server Components
- **Figure 13** — Hospital Server Class Diagram
- **Figure 14** — Hospital Server Entity Diagram
- **Figure 15** — Complete session data flow trace
- **Figure 16** — Trust Ledger membership admission sequence
- **Figure 17** — Cross-hospital data exchange sequence
- **Figure 18** — Emergency access sequence (App Server orchestrated)
- **Table 2** — Full API Gateway routing table
- **Table 3** — Redis session status transitions
