from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


class FileToolError(ValueError):
    pass


@dataclass(frozen=True)
class FileAgent:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def resolved_root(self) -> Path:
        return self.root.resolve()

    def resolve_path(self, raw_path: str | None) -> Path:
        text = (raw_path or "").strip().replace("\\", "/")
        if not text:
            raise FileToolError("Ruta requerida.")
        path = Path(text)
        if path.is_absolute():
            raise FileToolError("Use rutas relativas dentro del espacio autorizado.")
        candidate = (self.resolved_root / path).resolve(strict=False)
        if not candidate.is_relative_to(self.resolved_root):
            raise FileToolError("Ruta bloqueada: sale del espacio autorizado.")
        return candidate

    async def create_directory(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve_path(payload.get("path"))
        target.mkdir(parents=True, exist_ok=True)
        return {"created": target.is_dir(), "path": str(target)}

    async def list_directory(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve_path(payload.get("path") or ".")
        if not target.exists() or not target.is_dir():
            raise FileToolError("La carpeta no existe dentro del espacio autorizado.")
        items = []
        for child in sorted(
            target.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        ):
            resolved = child.resolve(strict=False)
            if not resolved.is_relative_to(self.resolved_root):
                continue
            items.append(
                {
                    "name": child.name,
                    "path": str(child.relative_to(self.resolved_root)),
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"path": str(target), "items": items}

    async def create_text_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve_path(payload.get("path"))
        if target.exists() and not payload.get("overwrite"):
            raise FileToolError("El archivo ya existe; no se sobrescribio.")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(payload.get("content") or "")
        target.write_text(content, encoding="utf-8")
        return {"created": target.exists(), "path": str(target), "bytes": target.stat().st_size}

    async def read_text_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve_path(payload.get("path"))
        if not target.exists() or not target.is_file():
            raise FileToolError("El archivo no existe dentro del espacio autorizado.")
        if target.stat().st_size > 200_000:
            raise FileToolError("Archivo demasiado grande para leerlo en esta version.")
        return {"path": str(target), "content": target.read_text(encoding="utf-8")}

    async def append_text_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve_path(payload.get("path"))
        if not target.exists() or not target.is_file():
            raise FileToolError("El archivo debe existir para anadir contenido.")
        content = str(payload.get("content") or "")
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return {"updated": True, "path": str(target), "bytes": target.stat().st_size}

    async def rename(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = self.resolve_path(payload.get("source"))
        target = self.resolve_path(payload.get("target"))
        if not source.exists():
            raise FileToolError("El origen no existe.")
        if target.exists():
            raise FileToolError("El destino ya existe; no se sobrescribio.")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return {"renamed": target.exists(), "source": str(source), "target": str(target)}

    async def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").strip().lower()
        if len(query) < 2:
            raise FileToolError("La busqueda necesita al menos dos caracteres.")
        base = self.resolve_path(payload.get("path") or ".")
        if not base.exists() or not base.is_dir():
            raise FileToolError("La carpeta de busqueda no existe.")
        matches = []
        for child in base.rglob("*"):
            if len(matches) >= 50:
                break
            resolved = child.resolve(strict=False)
            if resolved.is_relative_to(self.resolved_root) and query in child.name.lower():
                matches.append(
                    {
                        "name": child.name,
                        "path": str(child.relative_to(self.resolved_root)),
                        "type": "directory" if child.is_dir() else "file",
                    }
                )
        return {"query": query, "matches": matches}

    async def scaffold_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise FileToolError("Nombre de proyecto requerido.")
        base = self.resolve_path(name)
        if base.exists():
            raise FileToolError("El proyecto ya existe; no se sobrescribio.")
        folders = ["docs", "src", "notes"]
        files = {
            "README.md": f"# {name}\n\nObjetivo:\n\nProximos pasos:\n",
            "notes/ideas.txt": "",
        }
        base.mkdir(parents=True)
        for folder in folders:
            (base / folder).mkdir(parents=True, exist_ok=True)
        for relative, content in files.items():
            (base / relative).write_text(content, encoding="utf-8")
        return {
            "created": True,
            "path": str(base),
            "items": folders + list(files),
        }


def register_file_tools(registry: ToolRegistry, agent: FileAgent) -> None:
    common = {
        "permissions_required": ["sesion autenticada", "ruta dentro del workspace autorizado"],
        "expected_result": "Ruta real verificada y resultado de la operacion.",
    }
    registry.register(
        ToolSpec(
            "files.create_directory",
            "Crear una carpeta nueva dentro del espacio de trabajo autorizado.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.create_directory,
            input_schema={"path": "ruta relativa"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.list_directory",
            "Listar archivos y carpetas dentro del espacio autorizado.",
            RiskLevel.QUERY,
            agent.list_directory,
            input_schema={"path": "ruta relativa opcional"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.create_text_file",
            "Crear un archivo de texto sin sobrescribir por defecto.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.create_text_file,
            input_schema={"path": "ruta relativa", "content": "texto", "overwrite": False},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.read_text_file",
            "Leer un archivo de texto pequeno dentro del espacio autorizado.",
            RiskLevel.QUERY,
            agent.read_text_file,
            input_schema={"path": "ruta relativa"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.append_text_file",
            "Anadir texto a un archivo existente autorizado.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.append_text_file,
            input_schema={"path": "ruta relativa", "content": "texto"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.rename",
            "Renombrar o mover un elemento sin sobrescribir destinos existentes.",
            RiskLevel.CONSEQUENCE,
            agent.rename,
            input_schema={"source": "ruta relativa", "target": "ruta relativa"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.search",
            "Buscar archivos por nombre dentro del espacio autorizado.",
            RiskLevel.QUERY,
            agent.search,
            input_schema={"path": "ruta relativa opcional", "query": "texto"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "files.scaffold_project",
            "Crear una estructura inicial segura para un proyecto.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.scaffold_project,
            input_schema={"name": "nombre de carpeta/proyecto"},
            **common,
        )
    )
