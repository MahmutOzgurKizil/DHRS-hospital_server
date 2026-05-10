from __future__ import annotations

import hashlib
import logging
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.trust_block import TrustLedgerBlock
from app.modules.ledger_interface import LedgerInterfaceModule
from app.storage.medical_id_table import MedicalIDTable, medical_id_table

logger = logging.getLogger(__name__)


class TrustVerificationError(Exception):
    pass


class CrossHospitalModule:
    """
    Fetches record indexes and individual records from peer hospitals over mTLS.

    SECURITY:
    - Trust Ledger verification is MANDATORY before any outbound request.
    - All fetched records are stored in the in-memory Medical ID Table only.
    - Cross-hospital records are NEVER written to the local Medical Database.
    """

    def __init__(
        self,
        ledger: LedgerInterfaceModule,
        table: MedicalIDTable = medical_id_table,
    ) -> None:
        self._ledger = ledger
        self._table = table

    async def request_record_index(
        self,
        session_id: str,
        patient_id_hash: str,
        target_hospitals: list[str],
        db: AsyncSession,
    ) -> list[dict]:
        """
        Queries each target hospital for a record index.
        Only hospitals verified in the Trust Ledger are contacted.
        Results are returned in memory; nothing is persisted locally.
        """
        all_records: list[dict] = []

        for hospital_id in target_hospitals:
            peer_pubkey = await self._get_peer_pubkey(db, hospital_id)
            if peer_pubkey is None:
                logger.warning("Trust Ledger has no JOIN entry for hospital %s", hospital_id)
                continue

            trusted = await self._ledger.verify_trust_ledger_for_peer(
                db, hospital_id, peer_pubkey
            )
            if not trusted:
                logger.warning("Hospital %s failed Trust Ledger verification", hospital_id)
                continue

            try:
                records = await self._fetch_index_from_peer(
                    hospital_id, peer_pubkey, session_id, patient_id_hash
                )
                all_records.extend(records)
            except Exception:
                logger.exception("Failed to fetch record index from %s", hospital_id)

        return all_records

    async def fetch_single_record(
        self,
        session_id: str,
        record_id: str,
        source_hospital: str,
        patient_id_hash: str,
        db: AsyncSession,
    ) -> dict:
        """
        Fetches one full record from a peer hospital.
        Stored in the Medical ID Table (memory only) for the session duration.
        """
        # Check if we already have it cached in memory
        cached = await self._table.get_cross_hospital_record(session_id, record_id)
        if cached is not None:
            return cached

        peer_pubkey = await self._get_peer_pubkey(db, source_hospital)
        if peer_pubkey is None:
            raise TrustVerificationError(f"No Trust Ledger entry for {source_hospital}")

        trusted = await self._ledger.verify_trust_ledger_for_peer(
            db, source_hospital, peer_pubkey
        )
        if not trusted:
            raise TrustVerificationError(f"Trust Ledger verification failed for {source_hospital}")

        record = await self._fetch_record_from_peer(
            source_hospital, peer_pubkey, record_id, session_id, patient_id_hash
        )
        # Store in memory only — never in medical_records DB table
        await self._table.add_cross_hospital_record(session_id, record_id, record)
        return record

    async def _get_peer_pubkey(self, db: AsyncSession, hospital_id: str) -> str | None:
        hospital_hash = hashlib.sha256(hospital_id.encode()).hexdigest()
        result = await db.execute(
            select(TrustLedgerBlock)
            .where(TrustLedgerBlock.subject_hospital == hospital_hash)
            .order_by(TrustLedgerBlock.block_index.desc())
            .limit(1)
        )
        block = result.scalar_one_or_none()
        if block is None or block.event_type != "JOIN":
            return None
        return block.subject_pubkey

    async def _fetch_index_from_peer(
        self,
        hospital_id: str,
        peer_pubkey_pem: str,
        session_id: str,
        patient_id_hash: str,
    ) -> list[dict]:
        payload = {
            "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest(),
            "patient_id_hash": patient_id_hash,
            "requesting_hospital_id": settings.hospital_id,
            "signature": self._sign_request(session_id, patient_id_hash),
        }
        async with self._build_mtls_client() as client:
            # Peer endpoint URL would come from a peer registry; using placeholder
            url = f"https://{hospital_id}/cross-hospital/data"
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("records", [])
            for r in records:
                r["source_hospital"] = hospital_id
            return records

    async def _fetch_record_from_peer(
        self,
        hospital_id: str,
        peer_pubkey_pem: str,
        record_id: str,
        session_id: str,
        patient_id_hash: str,
    ) -> dict:
        async with self._build_mtls_client() as client:
            url = f"https://{hospital_id}/cross-hospital/data/{record_id}"
            resp = await client.get(
                url,
                params={
                    "session_id_hash": hashlib.sha256(session_id.encode()).hexdigest(),
                    "patient_id_hash": patient_id_hash,
                },
            )
            resp.raise_for_status()
            return resp.json()

    def _build_mtls_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cert=(settings.client_cert_path, settings.client_key_path),
            verify=settings.mtls_ca_cert_path,
            http2=True,
            timeout=settings.cross_hospital_timeout_seconds,
        )

    @staticmethod
    def _sign_request(session_id: str, patient_id_hash: str) -> str:
        import base64
        import hmac as hmac_mod

        msg = f"{session_id}:{patient_id_hash}".encode()
        sig = hmac_mod.new(settings.kek_bytes, msg, hashlib.sha256).digest()
        return base64.b64encode(sig).decode()
