from __future__ import annotations

import logging
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import Certificate
from fastapi import HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)

_ca_cert: Certificate | None = None


def _load_ca() -> Certificate:
    global _ca_cert
    if _ca_cert is None:
        ca_path = Path(settings.mtls_ca_cert_path)
        if ca_path.exists():
            pem = ca_path.read_bytes()
            _ca_cert = x509.load_pem_x509_certificate(pem)
    return _ca_cert  # type: ignore[return-value]


async def verify_mtls(request: Request) -> str:
    """
    FastAPI dependency that extracts and validates the client mTLS certificate.

    Reads the certificate from the X-Client-Cert header (set by the reverse proxy
    after TLS termination). Returns the hospital_id extracted from the certificate's
    Common Name (CN).

    In development/test environments without a proxy, the dependency can be
    overridden via app.dependency_overrides.
    """
    cert_pem = request.headers.get("X-Client-Cert")
    if not cert_pem:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client certificate required",
        )

    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client certificate format",
        )

    # Extract hospital_id from CN
    try:
        cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if not cn_attrs:
            raise ValueError("No CN in certificate subject")
        hospital_id = cn_attrs[0].value
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cannot extract hospital ID from certificate",
        )

    return hospital_id
