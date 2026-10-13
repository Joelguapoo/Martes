from app.memory.models import MemoryCategory, MemoryItem
from app.memory.store import MemoryStore


def test_memory_persists_after_reopen(tmp_path):
    db = tmp_path / "memory.db"
    store = MemoryStore(db)
    item = store.add(
        MemoryItem(category=MemoryCategory.PROFILE, content="El usuario prefiere espanol.")
    )

    reopened = MemoryStore(db)
    results = reopened.search("espanol")

    assert results[0].id == item.id


def test_memory_update_and_delete(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    item = store.add(MemoryItem(category=MemoryCategory.PROJECT, content="Proyecto inicial"))

    updated = store.update(item.id, content="Proyecto X activo", category=MemoryCategory.PROJECT)
    assert updated is not None
    assert updated.content == "Proyecto X activo"
    assert store.delete(item.id) is True
    assert store.list() == []
