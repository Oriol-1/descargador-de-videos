"""Publica una nueva versión en GitHub (Release con el instalador y la versión portable).

Uso:
    1. Sube VERSION en empaquetado/construir.py
    2. python empaquetado/construir.py
    3. python empaquetado/publicar.py

Necesita la herramienta `gh` (GitHub CLI) con la sesión iniciada.
La página web enlaza a .../releases/latest/download/..., así que siempre
ofrece la última versión publicada sin tener que tocarla.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from construir import AQUI, NOMBRE, VERSION  # noqa: E402

# Nombres sin espacios: son los que usa el enlace de descarga de la web.
ARCHIVOS = {
    AQUI / f"Instalar {NOMBRE}.exe": "Instalar-Descargador-de-videos.exe",
    AQUI / f"{NOMBRE} (Windows).zip": "Descargador-de-videos-portable.zip",
}
# Código fuente de componentes GPL (exigido por su licencia). Se adjunta si está descargado.
for fuente in sorted((AQUI / "fuentes-terceros").glob("ffmpeg-*.tar.xz")):
    version_ff = fuente.name.removeprefix("ffmpeg-").removesuffix(".tar.xz")
    ARCHIVOS[fuente] = f"FFmpeg-{version_ff}-codigo-fuente.tar.xz"

NOTAS = f"""## Descargador de vídeos {VERSION}

**[⬇ Descargar el instalador](https://github.com/Oriol-1/descargador-de-videos/releases/download/v{VERSION}/Instalar-Descargador-de-videos.exe)** (Windows 10/11)

- **Instalar-Descargador-de-videos.exe**: instalador (menú Inicio, escritorio y desinstalación desde Configuración → Aplicaciones). No pide permisos de administrador.
- **Descargador-de-videos-portable.zip**: versión sin instalar; descomprime y abre `Descargador de videos.exe`.

Si Windows muestra «Windows protegió su PC», pulsa **Más información → Ejecutar de todas formas** (el programa no está firmado digitalmente).
"""


def main():
    faltan = [str(p) for p in ARCHIVOS if not p.exists()]
    if faltan:
        sys.exit("Faltan archivos, ejecuta antes construir.py:\n  " + "\n  ".join(faltan))
    with tempfile.TemporaryDirectory() as tmp:
        subidas = []
        for origen, nombre in ARCHIVOS.items():
            destino = Path(tmp) / nombre
            shutil.copy2(origen, destino)
            subidas.append(str(destino))
        subprocess.run(
            ["gh", "release", "create", f"v{VERSION}", *subidas,
             "--title", f"Descargador de vídeos {VERSION}", "--notes", NOTAS, "--latest"],
            check=True, cwd=AQUI.parent,
        )
    print(f"Publicada la versión {VERSION}.")


if __name__ == "__main__":
    main()
