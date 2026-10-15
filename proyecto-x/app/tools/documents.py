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
            required_params=["path"],
            purpose="Leer documentos de texto o Markdown autorizados.",
            appropriate_when=[
                "El usuario pide abrir, revisar o extraer contenido de un documento.",
            ],
            natural_examples=["Lee el documento plan.md", "Abre mis notas y dime qué dicen"],
            possible_errors=["Documento inexistente", "Formato no soportado"],
            verification="Devolver ruta y contenido con conteo de caracteres.",
            semantic_tags=["documento", "leer", "markdown", "txt"],
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
            required_params=["path"],
            purpose="Leer y validar un archivo JSON autorizado.",
            appropriate_when=["El usuario pide revisar o validar un JSON."],
            natural_examples=["Valida config.json", "Lee este JSON"],
            possible_errors=["JSON invalido", "Documento inexistente"],
            verification="Devolver datos parseados y tipo raiz.",
            semantic_tags=["json", "documento", "leer"],
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
            required_params=["path"],
            purpose="Leer una muestra de CSV autorizado.",
            appropriate_when=["El usuario pide inspeccionar una tabla CSV."],
            natural_examples=["Revisa datos.csv", "Muéstrame una muestra del CSV"],
            possible_errors=["Documento inexistente", "CSV vacio"],
            verification="Devolver encabezados y filas de muestra.",
            semantic_tags=["csv", "tabla", "documento"],
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
            required_params=["path"],
            purpose="Resumir de forma local documentos TXT, Markdown, JSON o CSV.",
            appropriate_when=[
                "El usuario pide resumen, análisis breve o vista rápida de un documento.",
            ],
            natural_examples=["Resume plan.md", "Analiza este archivo y dame lo importante"],
            possible_errors=["Formato no soportado", "Documento demasiado grande"],
            verification="Devolver ruta, conteos y resumen.",
            semantic_tags=["resumen", "analizar", "documento"],
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
            required_params=["path", "title", "body"],
            purpose="Crear un informe Markdown sin sobrescribir.",
            appropriate_when=["El usuario pide guardar un informe o documento Markdown."],
            natural_examples=["Crea un informe markdown", "Guarda el resumen en un .md"],
            possible_errors=["Documento ya existe", "Extension invalida"],
            verification="Confirmar documento creado, ruta y bytes.",
            semantic_tags=["informe", "markdown", "crear"],
            **common,
        )
    )
