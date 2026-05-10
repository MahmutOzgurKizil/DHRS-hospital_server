from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WriteRecordRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    record_type: str = Field(max_length=50)
    content: dict


class WriteRecordResponse(BaseModel):
    record_id: str
    content_hash: str
    ledger_block_index: int
