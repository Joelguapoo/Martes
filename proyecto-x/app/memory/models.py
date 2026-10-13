from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryCategory(StrEnum):
    PROFILE = "perfil"
    PREFERENCE = "preferencia"
    INTEREST = "interes_objetivo"
    PROJECT = "proyecto"
    EPISODIC = "episodica"
    TEMPORAL = "temporal"


class MemorySource(StrEnum):
    USER = "usuario"
    CONTEXT = "observacion_contextual"
    INFERENCE = "inferencia"


class ConfirmationStatus(StrEnum):
    CONFIRMED = "confirmado"
    PENDING = "pendiente"
    DISCARDED = "descartado"


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    category: MemoryCategory
    content: str = Field(min_length=1)
    source: MemorySource = MemorySource.USER
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    confirmation_status: ConfirmationStatus = ConfirmationStatus.CONFIRMED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
