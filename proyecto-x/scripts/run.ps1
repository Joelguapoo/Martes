$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv")) {
  Write-Host "No existe .venv. Crea el entorno con: py -3.11 -m venv .venv"
  exit 1
}
. .\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8765
