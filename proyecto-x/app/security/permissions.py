from enum import IntEnum


class RiskLevel(IntEnum):
    QUERY = 0
    LOCAL_REVERSIBLE = 1
    CONSEQUENCE = 2
    DESTRUCTIVE_OR_SENSITIVE = 3


class PermissionError(Exception):
    """Raised when an action exceeds the current authorization level."""


def require_authorization(risk_level: RiskLevel, authorized: bool) -> None:
    if risk_level >= RiskLevel.CONSEQUENCE and not authorized:
        raise PermissionError(
            "Esta accion requiere autorizacion explicita antes de ejecutarse."
        )
    if risk_level >= RiskLevel.DESTRUCTIVE_OR_SENSITIVE:
        raise PermissionError(
            "Las acciones destructivas o sensibles estan bloqueadas en esta version."
        )
