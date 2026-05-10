from __future__ import annotations

import base64
import json
import struct
from functools import cached_property
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


class DecryptionEngine:
    """
    Handles all cryptographic decryption operations.

    enc_pinfo format (hybrid encryption):
        [2B big-endian key_len][RSA-OAEP(HSK, aes_key)][12B nonce][AES-256-GCM(aes_key, pinfo_json)]

    enc_med_id format:
        RSA-OAEP(doctor_public_key, med_id_utf8_bytes)

    Doctor DSK at rest:
        [12B nonce][AES-256-GCM(KEK, doctor_private_key_pem)]
    """

    @cached_property
    def _hsk(self) -> RSAPrivateKey:
        pem_bytes = Path(settings.hsk_pem_path).read_bytes()
        return serialization.load_pem_private_key(pem_bytes, password=None)  # type: ignore[return-value]

    def decrypt_pinfo(self, enc_pinfo_b64: str) -> dict:
        """Decrypt patient info encrypted with the hospital's public key (HPK)."""
        try:
            raw = base64.b64decode(enc_pinfo_b64)
            (key_len,) = struct.unpack_from(">H", raw, 0)
            offset = 2
            rsa_encrypted_aes_key = raw[offset : offset + key_len]
            offset += key_len
            nonce = raw[offset : offset + 12]
            offset += 12
            ciphertext_and_tag = raw[offset:]

            aes_key = self._rsa_oaep_decrypt(self._hsk, rsa_encrypted_aes_key)
            plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext_and_tag, None)
            return json.loads(plaintext)
        except Exception as exc:
            raise ValueError(f"enc_pinfo decryption failed: {exc}") from exc

    def decrypt_med_id(self, enc_med_id_b64: str, encrypted_dsk: bytes) -> str:
        """
        Decrypt the patient's medical ID using the doctor's private key.

        The doctor's private key is stored encrypted under the KEK.
        We decrypt it here, use it once, then zero the key material.
        """
        dsk_pem: bytearray | None = None
        try:
            kek = settings.kek_bytes
            nonce = encrypted_dsk[:12]
            ciphertext_and_tag = encrypted_dsk[12:]
            dsk_pem_bytes = AESGCM(kek).decrypt(nonce, ciphertext_and_tag, None)

            dsk_pem = bytearray(dsk_pem_bytes)
            doctor_key: RSAPrivateKey = serialization.load_pem_private_key(  # type: ignore[assignment]
                bytes(dsk_pem), password=None
            )
            enc_med_id = base64.b64decode(enc_med_id_b64)
            med_id_bytes = self._rsa_oaep_decrypt(doctor_key, enc_med_id)
            return med_id_bytes.decode("utf-8")
        except Exception as exc:
            raise ValueError(f"enc_med_id decryption failed: {exc}") from exc
        finally:
            # Zero the PEM bytes regardless of success or failure
            if dsk_pem is not None:
                for i in range(len(dsk_pem)):
                    dsk_pem[i] = 0

    @staticmethod
    def _rsa_oaep_decrypt(private_key: RSAPrivateKey, ciphertext: bytes) -> bytes:
        return private_key.decrypt(
            ciphertext,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )


_engine: DecryptionEngine | None = None


def get_decryption_engine() -> DecryptionEngine:
    global _engine
    if _engine is None:
        _engine = DecryptionEngine()
    return _engine
