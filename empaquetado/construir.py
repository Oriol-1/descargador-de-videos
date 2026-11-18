"""Construye la versión portable (.exe) del descargador.

Uso:  python empaquetado/construir.py

Resultado:
    empaquetado/dist/Descargador de videos/            carpeta con el .exe
    empaquetado/Descargador de videos (Windows).zip    versión portable (sin instalar)
    empaquetado/Instalar Descargador de videos.exe     instalador (necesita Inno Setup 6)

Opcional: si dejas deno.exe en empaquetado/bin/, se incluye dentro del programa
(así funciona en PCs que no tengan Node ni Deno instalados).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROYECTO = AQUI.parent
NOMBRE = "Descargador de videos"
BIN = AQUI / "bin"
ICONO = AQUI / "icono.ico"
DIST = AQUI / "dist"
VERSION = "1.0.3"  # súbela al publicar cambios: el instalador actualiza la versión anterior
INNO = [Path(os.environ.get(v, "")) / "Inno Setup 6" / "ISCC.exe"
        for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
INNO.insert(2, Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe")

LEEME = """Descargador de vídeos
=====================

Cómo usarlo
-----------
1. Abre "Descargador de videos.exe" (no lo saques de esta carpeta).
2. Elige "Descargar un vídeo" o "Unir dos vídeos".
3. Pega el enlace (o los dos) y pulsa Descargar.

Los vídeos se guardan en Descargas\\Descargador de vídeos (se puede cambiar).

Si Windows muestra "Windows protegió su PC", pulsa "Más información" y
"Ejecutar de todas formas": aparece porque el programa no está firmado.

Si algún día deja de descargar, pulsa "Buscar actualización": las webs cambian
a menudo y el motor de descarga (yt-dlp) se actualiza para seguir funcionando.

USO RESPONSABLE: usa el programa solo con contenido que tengas derecho a
descargar (tus propios vídeos, dominio público, licencias que lo permitan o
con permiso del titular). Proyecto personal sin ánimo de lucro.

Documentos legales incluidos en esta carpeta
--------------------------------------------
- TERMINOS.txt               Términos de uso (debes aceptarlos para usarlo)
- LICENSE                    Licencia del programa (MIT, sin garantías)
- LICENCIAS-TERCEROS.txt     Componentes incluidos y sus licencias
- licencias\\                 Textos completos de esas licencias

Más información: https://oriol-1.github.io/descargador-de-videos/legal/
"""


def copiar_documentos_legales(carpeta):
    """Copia términos y licencias junto al programa (TERMINOS con BOM para Inno Setup)."""
    texto = (PROYECTO / "TERMINOS.txt").read_text(encoding="utf-8")
    (carpeta / "TERMINOS.txt").write_text(texto, encoding="utf-8-sig")
    shutil.copy2(PROYECTO / "LICENSE", carpeta / "LICENSE")
    shutil.copy2(PROYECTO / "LICENCIAS-TERCEROS.txt", carpeta / "LICENCIAS-TERCEROS.txt")
    shutil.copytree(AQUI / "licencias", carpeta / "licencias", dirs_exist_ok=True)


def crear_icono():
    if ICONO.exists():
        return
    from PIL import Image, ImageDraw

    lado = 256
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, lado - 8, lado - 8), radius=56, fill=(220, 38, 38))
    blanco = (255, 255, 255)
    d.rectangle((108, 52, 148, 140), fill=blanco)            # palo de la flecha
    d.polygon([(68, 128), (188, 128), (128, 192)], fill=blanco)  # punta
    d.rounded_rectangle((64, 204, 192, 222), radius=8, fill=blanco)  # base
    img.save(ICONO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def preparar_binarios():
    import imageio_ffmpeg

    BIN.mkdir(exist_ok=True)
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), BIN / "ffmpeg.exe")
    return [p for p in (BIN / "ffmpeg.exe", BIN / "deno.exe") if p.exists()]


def main():
    crear_icono()
    binarios = preparar_binarios()
    if not (BIN / "deno.exe").exists():
        print("AVISO: no hay empaquetado/bin/deno.exe. El programa usará Node o Deno si el PC "
              "los tiene instalados; si no, algunas webs pueden dar menos calidades o fallar.")

    comando = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", NOMBRE,
        "--icon", str(ICONO),
        "--add-data", f"{ICONO};.",
        "--copy-metadata", "yt-dlp",
        "--exclude-module", "imageio_ffmpeg",
        "--distpath", str(DIST),
        "--workpath", str(Path(tempfile.gettempdir()) / "descargador_build"),
        "--specpath", str(AQUI),
    ]
    for b in binarios:
        comando += ["--add-binary", f"{b};bin"]
    comando.append(str(PROYECTO / "descargador_gui.py"))
    subprocess.run(comando, check=True)
    (BIN / "ffmpeg.exe").unlink(missing_ok=True)  # copia temporal, ya está dentro del programa

    carpeta = DIST / NOMBRE
    (carpeta / "LEEME.txt").write_text(LEEME, encoding="utf-8")
    copiar_documentos_legales(carpeta)

    zip_final = AQUI / f"{NOMBRE} (Windows).zip"
    zip_final.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_final, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for archivo in carpeta.rglob("*"):
            z.write(archivo, Path(NOMBRE) / archivo.relative_to(carpeta))
    print(f"\nListo:\n  {carpeta / (NOMBRE + '.exe')}\n  {zip_final} "
          f"({zip_final.stat().st_size / 1e6:.0f} MB)")

    iscc = next((p for p in INNO if p.exists()), None)
    if not iscc:
        print("AVISO: no está Inno Setup 6 instalado; no se genera el instalador.")
        return
    subprocess.run([str(iscc), "/Q", f"/DVersion={VERSION}", str(AQUI / "instalador.iss")],
                   check=True, cwd=AQUI)
    instalador = AQUI / f"Instalar {NOMBRE}.exe"
    print(f"  {instalador} ({instalador.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
