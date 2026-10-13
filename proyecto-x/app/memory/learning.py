import re

from app.memory.models import ConfirmationStatus, MemoryCategory, MemoryItem, MemorySource

PREFERENCE_PATTERNS = [
    re.compile(r"\bme gusta que\b(?P<content>.+)", re.IGNORECASE),
    re.compile(r"\bprefiero\b(?P<content>.+)", re.IGNORECASE),
    re.compile(r"\bllamame\b(?P<content>.+)", re.IGNORECASE),
]


def propose_memories(user_text: str, auto_learning: bool) -> list[MemoryItem]:
    proposals: list[MemoryItem] = []
    for pattern in PREFERENCE_PATTERNS:
        match = pattern.search(user_text)
        if not match:
            continue
        content = match.group("content").strip(" .:")
        if len(content) < 3:
            continue
        proposals.append(
            MemoryItem(
                category=MemoryCategory.PREFERENCE,
                content=f"El usuario indico una preferencia: {content}",
                source=MemorySource.USER,
                confidence=0.85,
                confirmation_status=(
                    ConfirmationStatus.CONFIRMED
                    if auto_learning
                    else ConfirmationStatus.PENDING
                ),
            )
        )
    return proposals[:3]
