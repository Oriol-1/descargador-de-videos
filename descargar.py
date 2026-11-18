"""Descarga videos de YouTube en la maxima calidad disponible.

Uso:
    python descargar.py                 -> pide el enlace por teclado
    python descargar.py <enlace> [...]  -> descarga uno o varios enlaces
"""

import sys
from pathlib import Path

try:
    import yt_dlp
    import imageio_ffmpeg
except ImportError:
    print("Faltan dependencias. Ejecuta:")
    print("    python -m pip install --upgrade yt-dlp imageio-ffmpeg")
    sys.exit(1)

CARPETA = Path(__file__).resolve().parent / "descargas"


def progreso(d):
    if d["status"] == "downloading":
        porcentaje = d.get("_percent_str", "").strip()
        velocidad = d.get("_speed_str", "").strip()
        restante = d.get("_eta_str", "").strip()
        print(f"\r  {porcentaje}  a {velocidad}  quedan {restante}      ", end="", flush=True)
    elif d["status"] == "finished":
        print("\r  Descarga completa, procesando...                      ")


def descargar(enlace):
    CARPETA.mkdir(exist_ok=True)
    opciones = {
        # Mejor video + mejor audio por separado y se unen; si no, el mejor archivo unico.
        "format": "bestvideo*+bestaudio/best",
        # Prioriza resolucion, luego fps, luego bitrate (sin limitar el codec).
        "format_sort": ["res", "fps", "br"],
        "merge_output_format": "mkv",  # mkv admite cualquier codec (AV1, VP9, H.264...)
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "outtmpl": str(CARPETA / "%(title)s [%(height)sp].%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "progress_hooks": [progreso],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(enlace, download=False)
        print(f"\nTitulo:  {info.get('title')}")
        print(f"Calidad: {info.get('width')}x{info.get('height')} a {info.get('fps')} fps")
        ydl.download([enlace])
    print(f"  Guardado en: {CARPETA}")


def main():
    enlaces = sys.argv[1:]
    interactivo = not enlaces
    while True:
        if interactivo:
            texto = input("\nPega el enlace de YouTube (Enter vacio para salir): ").strip()
            if not texto:
                break
            enlaces = [texto]
        for enlace in enlaces:
            try:
                descargar(enlace)
            except yt_dlp.utils.DownloadError as e:
                print(f"\n  Error: {e}")
        if not interactivo:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.")
