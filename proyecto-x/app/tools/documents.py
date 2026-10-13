from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from typing import Any

from app.security.permissions import RiskLevel
from app.tools.files import FileAgent
from app.tools.registry import ToolRegistry, ToolSpec


class DocumentToolError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentAgent:
    files: FileAgent
    max_bytes: int = 300_000

    def _read_text(self, raw_path: str | None) -> tuple[str, str]:
        target = self.files.resolve_path(raw_path)
        if not target.exists() or not target.is_file():
            raise DocumentToolError("El documento no existe dentro del espacio autorizado.")
        if target.stat().st_size > self.max_bytes:
            raise DocumentToolError("Documento demasiado grande para procesarlo en esta version.")
        suffix = target.suffix.lower()
        if suffix not in {".txt", ".md", ".json", ".csv"}:
            raise DocumentToolError(
                "Formato no soportado en esta version. PDF, DOCX y XLSX requieren fase adicional."
            )
        return str(target), target.read_text(encoding="utf-8")

    async def read_text_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, content = self._read_text(payload.get("path"))
        return {"path": path, "content": content, "characters": len(content)}

    async def read_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, content = self._read_text(payload.get("path"))
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DocumentToolError(f"JSON invalido: {exc.msg}") from exc
        return {"path": path, "type": type(parsed).__name__, "data": parsed}

    async def read_csv(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, content = self._read_text(payload.get("path"))
        rows = list(csv.DictReader(content.splitlines()))
        if not rows:
            sample_rows = list(csv.reader(content.splitlines()))
            headers = sample_rows[0] if sample_rows else []
            rows = [dict(enumerate(row)) for row in sample_rows[1:51]]
        else:
            headers = list(rows[0].keys())
            rows = rows[:50]
        return {"path": path, "headers": headers, "rows": rows, "row_count_sample": len(rows)}

    async def summarize(self, payload: dict[str, Any]) -> dict[str, Any]:
        path, content = self._read_text(payload.get("path"))
        words = content.split()
        preview = " ".join(words[:120])
        return {
            "path": path,
            "characters": len(content),
            "words": len(words),
            "summary": preview or "Documento vacío.",
        }

    async def create_markdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self.files.resolve_path(payload.get("path"))
        if target.suffix.lower() not in {".md", ".markdown"}:
            raise DocumentToolError("La ruta debe terminar en .md o .markdown.")
        if target.exists() and not payload.get("overwrite"):
            raise DocumentToolError("El documento ya existe; no se sobrescribio.")
        target.parent.mkdir(parents=True, exist_ok=True)
        title = str(payload.get("title") or "Informe MARTES").strip()
        body = str(payload.get("body") or "").strip()
        content = f"# {title}\n\n{body}\n"
        target.write_text(content, encoding="utf-8")
        return {
            "created": target.exists(),
            "path": str(target),
            "bytes": target.stat().st_size,
            "characters": len(content),
        }


def register_document_tools(registry: ToolRegistry, agent: DocumentAgent) -> None:
    common = {
        "permissions_required": ["sesion autenticada", "ruta dentro del workspace autorizado"],
        "expected_result": "Documento leído o creado con ruta verificada.",
    }
    registry.register(
        ToolSpec(
            "documents.read_text",
            "Leer documentos TXT o Markdown autorizados.",
            RiskLevel.QUERY,
            agent.read_text_document,
            input_schema={"path": "ruta relativa .txt/.md"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "documents.read_json",
            "Leer y validar documentos JSON autorizados.",
            RiskLevel.QUERY,
            agent.read_json,
            input_schema={"path": "ruta relativa .json"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "documents.read_csv",
            "Leer una muestra de CSV autorizado.",
            RiskLevel.QUERY,
            agent.read_csv,
            input_schema={"path": "ruta relativa .csv"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "documents.summarize",
            "Preparar un resumen local basico de TXT, Markdown, JSON o CSV.",
            RiskLevel.QUERY,
            agent.summarize,
            input_schema={"path": "ruta relativa"},
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "documents.create_markdown",
            "Crear un informe Markdown sin sobrescribir por defecto.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.create_markdown,
            input_schema={"path": "ruta relativa .md", "title": "titulo", "body": "contenido"},
            **common,
        )
    )
