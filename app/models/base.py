import uuid
from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase

try:
    from sqlalchemy.dialects.postgresql import JSONB as _PGjsonb
    JSONB = _PGjsonb().with_variant(JSON(), "sqlite")
except ImportError:
    JSONB = JSON()


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass
