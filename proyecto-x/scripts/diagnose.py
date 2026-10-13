import platform
import shutil
import subprocess
import sys


def main() -> int:
    print("Proyecto X - diagnostico local")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Sistema: {platform.platform()}")
    print(f"Procesador: {platform.processor() or 'no detectado'}")
    ollama = shutil.which("ollama")
    print(f"Ollama: {ollama or 'no instalado/en PATH'}")
    if ollama:
        try:
            result = subprocess.run(
                [ollama, "list"],
                check=False,
                text=True,
                capture_output=True,
                timeout=5,
            )
            print(result.stdout or result.stderr)
        except Exception as exc:
            print(f"No pude consultar Ollama: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
