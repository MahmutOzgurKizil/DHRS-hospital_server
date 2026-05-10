"""
Lazy singleton providers for all internal modules.

All providers are used as FastAPI Depends() targets.
Tests override them via app.dependency_overrides[get_X] = lambda: mock_X.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.appointment_module import AppointmentModule
    from app.modules.cross_hospital import CrossHospitalModule
    from app.modules.data_retrieval import DataRetrievalModule
    from app.modules.data_write import DataWriteModule
    from app.modules.decryption_engine import DecryptionEngine
    from app.modules.ledger_interface import LedgerInterfaceModule
    from app.modules.session_mapping import SessionMappingModule
    from app.modules.session_termination import SessionTerminationModule

_ledger: LedgerInterfaceModule | None = None
_mapping: SessionMappingModule | None = None
_retrieval: DataRetrievalModule | None = None
_data_write: DataWriteModule | None = None
_cross_hospital: CrossHospitalModule | None = None
_termination: SessionTerminationModule | None = None
_appointment: AppointmentModule | None = None


def get_ledger() -> LedgerInterfaceModule:
    global _ledger
    if _ledger is None:
        from app.modules.ledger_interface import LedgerInterfaceModule
        _ledger = LedgerInterfaceModule()
    return _ledger


def get_session_mapping() -> SessionMappingModule:
    global _mapping
    if _mapping is None:
        from app.modules.session_mapping import SessionMappingModule
        _mapping = SessionMappingModule()
    return _mapping


def get_retrieval() -> DataRetrievalModule:
    global _retrieval
    if _retrieval is None:
        from app.modules.data_retrieval import DataRetrievalModule
        _retrieval = DataRetrievalModule()
    return _retrieval


def get_data_write() -> DataWriteModule:
    global _data_write
    if _data_write is None:
        from app.modules.data_write import DataWriteModule
        _data_write = DataWriteModule(get_retrieval(), get_ledger())
    return _data_write


def get_cross_hospital() -> CrossHospitalModule:
    global _cross_hospital
    if _cross_hospital is None:
        from app.modules.cross_hospital import CrossHospitalModule
        _cross_hospital = CrossHospitalModule(get_ledger())
    return _cross_hospital


def get_termination() -> SessionTerminationModule:
    global _termination
    if _termination is None:
        from app.modules.session_termination import SessionTerminationModule
        from app.storage.medical_id_table import medical_id_table
        _termination = SessionTerminationModule(medical_id_table, get_ledger())
    return _termination


def get_appointment() -> AppointmentModule:
    global _appointment
    if _appointment is None:
        from app.modules.appointment_module import AppointmentModule
        _appointment = AppointmentModule()
    return _appointment


def get_decryption_engine() -> DecryptionEngine:
    from app.modules.decryption_engine import get_decryption_engine as _get
    return _get()
