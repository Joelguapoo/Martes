from enum import StrEnum

from pydantic import BaseModel, Field


class RequestKind(StrEnum):
    CONVERSATION = "conversacion"
    MEMORY = "memoria"
    RESEARCH = "investigacion"
    PLANNING = "planificacion"
    AUTHORIZED_ACTION = "accion_autorizada"
    UNAVAILABLE = "no_disponible"


class AssistantPlan(BaseModel):
    kind: RequestKind
    objective: str = Field(min_length=1)
    steps: list[str] = Field(default_factory=list)
    needs_authorization: bool = False


class PlanStatus(StrEnum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
