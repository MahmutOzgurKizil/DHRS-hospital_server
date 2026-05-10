from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://dhrs:dhrs@localhost:5432/hospital_db"
    redis_url: str = "redis://localhost:6379/0"
    hsk_pem_path: str = "certs/hospital_secret_key.pem"
    kek_hex: str = "ab" * 32  # must be overridden in production
    hospital_id: str = "hospital-default"
    mtls_ca_cert_path: str = "certs/ca.crt"
    session_ttl_seconds: int = 3600
    memory_table_ttl_seconds: int = 3600
    cross_hospital_timeout_seconds: int = 10
    log_level: str = "INFO"
    # mTLS client cert paths (for outbound cross-hospital calls)
    client_cert_path: str = "certs/server.crt"
    client_key_path: str = "certs/server.key"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def kek_bytes(self) -> bytes:
        if len(self.kek_hex) != 64:
            raise ValueError("KEK_HEX must be exactly 64 hex characters (32 bytes)")
        return bytes.fromhex(self.kek_hex)


settings = Settings()
