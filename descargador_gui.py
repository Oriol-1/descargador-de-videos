"""Descargador de vídeos con interfaz gráfica.

- Modo "un vídeo"  -> descarga el vídeo en MP4 (H.264 + AAC, hasta 1080p).
- Modo "unir dos"  -> descarga ambos y los une en un único MP4 (primero el 1, luego el 2).

Funciona como script (python descargador_gui.py) y empaquetado como .exe (PyInstaller).
"""

import hashlib
import importlib.metadata
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Algunos programas (instaladores, depuradores, antivirus) dejan SSLKEYLOGFILE apuntando
# a un archivo en el que no se puede escribir; Python intenta abrirlo en cada conexión
# segura y todas fallan («Unable to handle request … unexpected error(s)»).
# El programa no necesita ese registro de claves, así que se ignora.
os.environ.pop("SSLKEYLOGFILE", None)

APP ="Descargador de vídeos"
CONGELADO = getattr(sys, "frozen", False)  # True dentro del .exe
RECURSOS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DIR_PROGRAMA = Path(sys.executable).parent if CONGELADO else Path(__file__).resolve().parent
DATOS = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DescargadorVideos"
EXTRA = DATOS / "ytdlp"  # yt-dlp actualizado (tiene prioridad sobre el incluido)
CONFIG = DATOS / "config.json"
SIN_VENTANA = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
URL_LEGAL = "https://oriol-1.github.io/descargador-de-videos/legal/"
TERMINOS_VERSION = "2026-11-18"  # cambiarla obliga a volver a aceptar los términos
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def version_tupla(texto):
    return tuple(int(x) for x in re.findall(r"\d+", texto or ""))


def preparar_ytdlp():
    """Si el usuario actualizó yt-dlp y es más nuevo que el incluido, se usa ese."""
    archivo = EXTRA / "yt_dlp" / "version.py"
    if not archivo.exists():
        return
    m = re.search(r"__version__\s*=\s*'([^']+)'", archivo.read_text(encoding="utf-8"))
    try:
        incluida = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        incluida = "0"
    if m and version_tupla(m.group(1)) > version_tupla(incluida):
        sys.path.insert(0, str(EXTRA))


preparar_ytdlp()

try:
    import yt_dlp
    import yt_dlp.version
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Faltan dependencias",
        "Abre una terminal y ejecuta:\n\n"
        'python -m pip install --upgrade "yt-dlp[default]" imageio-ffmpeg',
    )
    sys.exit(1)


def buscar_binario(nombre):
    """Busca un ejecutable incluido con el programa (carpeta bin)."""
    for carpeta in (RECURSOS / "bin", DIR_PROGRAMA / "bin"):
        ruta = carpeta / f"{nombre}.exe"
        if ruta.exists():
            return ruta
    return None


def ruta_ffmpeg():
    incluido = buscar_binario("ffmpeg")
    if incluido:
        return str(incluido)
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def motores_js():
    """Motores JavaScript que yt-dlp puede necesitar (incluidos o instalados)."""
    motores = {}
    for nombre in ("deno", "node", "bun"):
        incluido = buscar_binario(nombre)
        if incluido:
            motores[nombre] = {"path": str(incluido)}
        elif shutil.which(nombre):
            motores[nombre] = {}
    return motores or {"deno": {}}


FFMPEG = ruta_ffmpeg()
MOTORES_JS = motores_js()

# H.264 + AAC hasta 1080p (MP4 compatible con todo); si no existe, lo mejor posible.
FORMATO = (
    "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]"
    "/b[vcodec^=avc1][height<=1080][acodec^=mp4a]"
    "/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
)


# --------------------------------------------------------------------------- configuración

def carpeta_por_defecto():
    if CONGELADO:
        return Path.home() / "Downloads" / APP
    return DIR_PROGRAMA / "descargas"


def leer_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_config(datos):
    try:
        DATOS.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- actualización de yt-dlp

def _pypi(ruta):
    peticion = urllib.request.Request(f"https://pypi.org/pypi/{ruta}/json",
                                      headers={"User-Agent": "DescargadorVideos"})
    with urllib.request.urlopen(peticion, timeout=20) as r:
        return json.load(r)


def ultima_version():
    datos = _pypi("yt-dlp")
    return datos["info"]["version"], datos


def _descargar_rueda(datos_pypi, destino):
    rueda = next(u for u in datos_pypi["urls"]
                 if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("none-any.whl"))
    with urllib.request.urlopen(rueda["url"], timeout=120) as r:
        contenido = r.read()
    if hashlib.sha256(contenido).hexdigest() != rueda["digests"]["sha256"]:
        raise RuntimeError(f"El archivo {rueda['filename']} llegó dañado (sha256 no coincide).")
    raiz = destino.resolve()
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        for nombre in z.namelist():
            if nombre.endswith("/") or nombre.split("/")[0] not in ("yt_dlp", "yt_dlp_ejs"):
                continue
            ruta = (destino / nombre).resolve()
            if not ruta.is_relative_to(raiz):
                continue
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(z.read(nombre))


def instalar_actualizacion(datos_pypi):
    """Descarga yt-dlp y su yt-dlp-ejs compatible desde PyPI a la carpeta EXTRA."""
    nuevo = DATOS / "ytdlp_nuevo"
    shutil.rmtree(nuevo, ignore_errors=True)
    nuevo.mkdir(parents=True)
    _descargar_rueda(datos_pypi, nuevo)
    for req in datos_pypi["info"].get("requires_dist") or []:
        m = re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)", req)
        if m:
            _descargar_rueda(_pypi(f"yt-dlp-ejs/{m.group(1)}"), nuevo)
            break
    shutil.rmtree(EXTRA, ignore_errors=True)
    nuevo.rename(EXTRA)


def reiniciar():
    comando = [sys.executable] if CONGELADO else [sys.executable, str(Path(__file__).resolve())]
    subprocess.Popen(comando, creationflags=SIN_VENTANA)


# --------------------------------------------------------------------------- utilidades

class Cancelado(Exception):
    pass


def limpiar_nombre(texto, largo=80):
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", texto or "").strip(" .")
    return texto[:largo].rstrip(" .") or "video"


def ruta_libre(carpeta, nombre):
    destino = carpeta / f"{nombre}.mp4"
    n = 2
    while destino.exists():
        destino = carpeta / f"{nombre} ({n}).mp4"
        n += 1
    return destino


REGISTRO = DATOS / "registro.log"
_bloqueo_registro = threading.Lock()


def anotar(texto):
    """Guarda una línea en el registro de diagnóstico local (nunca sale del equipo)."""
    try:
        with _bloqueo_registro:
            DATOS.mkdir(parents=True, exist_ok=True)
            if REGISTRO.exists() and REGISTRO.stat().st_size > 512_000:  # rotación simple
                REGISTRO.replace(REGISTRO.with_suffix(".log.1"))
            with REGISTRO.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {texto}\n")
    except OSError:
        pass


ERRORES_CONOCIDOS = [
    (("Private video",), "El vídeo es privado: solo su autor puede verlo."),
    (("Sign in to confirm your age", "age-restricted", "inappropriate for some users"),
     "El vídeo tiene restricción de edad y la web exige iniciar sesión para verlo."),
    (("members-only", "Join this channel"), "El vídeo es solo para miembros del canal."),
    (("not available in your country", "geo restrict", "not made this video available"),
     "El vídeo no está disponible en tu país."),
    (("live event will begin", "Premieres in", "is upcoming"), "El vídeo todavía no se ha estrenado."),
    (("Video unavailable", "This video is unavailable", "has been removed", "This video has been"),
     "El vídeo no está disponible (puede haber sido eliminado o ser privado)."),
    # Fallo interno de conexión (no es culpa del enlace): va antes que «Unsupported URL:».
    (("Unable to handle request", "Unexpected error", "No supporting handlers"),
     "No se pudo establecer la conexión. Vuelve a intentarlo; si se repite, comprueba tu "
     "conexión a internet o el antivirus."),
    (("Unsupported URL:", "is not a valid URL"), "Ese enlace no es de un vídeo compatible."),
    (("getaddrinfo failed", "Unable to download webpage", "timed out", "No route to host",
      "Network is unreachable", "Connection refused", "Connection reset"),
     "No se pudo conectar. Comprueba tu conexión a internet y vuelve a intentarlo."),
    (("HTTP Error 403",),
     "El servidor rechazó la descarga. Vuelve a intentarlo en un momento; si sigue fallando, "
     "pulsa «Buscar actualización»."),
    (("No space left", "Errno 28"), "No queda espacio en el disco."),
    (("Requested format is not available",),
     "La web no ofrece este vídeo en un formato descargable. Prueba «Buscar actualización»."),
]


def explicar_error(texto):
    """Traduce los errores habituales de yt-dlp a un mensaje claro en español."""
    for claves, mensaje in ERRORES_CONOCIDOS:
        if any(c.lower() in texto.lower() for c in claves):
            return mensaje
    return texto


def par(valor):
    valor = int(valor or 0)
    return valor - valor % 2


# --------------------------------------------------------------------------- trabajo

class Trabajo:
    """Hace la descarga/unión en un hilo y envía mensajes a la interfaz por una cola."""

    def __init__(self, enlaces, carpeta, cola):
        self.enlaces = enlaces
        self.carpeta = carpeta
        self.cola = cola
        self.cancelado = threading.Event()
        self.proceso = None
        self.tramo = (0.0, 100.0)  # parte de la barra global que ocupa la fase actual

    # ---- mensajes a la interfaz
    def log(self, texto):
        self.cola.put(("log", texto))
        anotar(texto)

    def estado(self, texto):
        self.cola.put(("estado", texto))

    def avance(self, fraccion):
        ini, fin = self.tramo
        self.cola.put(("progreso", ini + (fin - ini) * max(0.0, min(1.0, fraccion))))

    def cancelar(self):
        self.cancelado.set()
        if self.proceso and self.proceso.poll() is None:
            self.proceso.kill()

    # ---- flujo principal
    def ejecutar(self):
        temp = None
        try:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            for viejo in self.carpeta.glob("_temp_*"):  # restos de cierres bruscos
                if viejo.is_dir() and time.time() - viejo.stat().st_mtime > 3600:
                    shutil.rmtree(viejo, ignore_errors=True)
            temp = self.carpeta / f"_temp_{int(time.time())}"
            temp.mkdir()
            if len(self.enlaces) == 1:
                self.un_video(temp)
            else:
                self.dos_videos(temp)
        except Cancelado:
            self.cola.put(("fin", (False, "Operación cancelada.")))
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            if self.cancelado.is_set():
                self.cola.put(("fin", (False, "Operación cancelada.")))
            else:
                anotar("ERROR " + "".join(traceback.format_exception(e)).strip())
                original = ANSI.sub("", str(e)).replace("ERROR: ", "").strip() or repr(e)
                explicado = explicar_error(original)
                if explicado != original:
                    self.log(f"Detalle técnico: {original}")
                self.cola.put(("fin", (False, explicado)))
        finally:
            if temp:
                shutil.rmtree(temp, ignore_errors=True)

    def un_video(self, temp):
        self.tramo = (0, 90)
        info = self.descargar(self.enlaces[0], temp, "parte1", "")
        destino = ruta_libre(self.carpeta, limpiar_nombre(info["title"]))
        self.tramo = (90, 100)
        if info["h264_aac"]:
            shutil.move(info["archivo"], destino)
        else:
            self.estado("Convirtiendo a MP4 compatible (H.264)...")
            self.log("El vídeo no está en H.264; se convierte para máxima compatibilidad.")
            self.ffmpeg(
                ["-i", info["archivo"], "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", str(destino)],
                info["duracion"],
            )
        self.avance(1)
        self.cola.put(("fin", (True, destino)))

    def dos_videos(self, temp):
        self.tramo = (0, 40)
        v1 = self.descargar(self.enlaces[0], temp, "parte1", "Vídeo 1/2: ")
        self.tramo = (40, 80)
        v2 = self.descargar(self.enlaces[1], temp, "parte2", "Vídeo 2/2: ")

        nombre = f"{limpiar_nombre(v1['title'], 60)} + {limpiar_nombre(v2['title'], 60)}"
        destino = ruta_libre(self.carpeta, nombre)
        self.tramo = (80, 100)

        if self.compatibles(v1, v2):
            self.estado("Uniendo vídeos (sin perder calidad)...")
            self.log("Ambos vídeos tienen el mismo formato: unión directa.")
            lista = temp / "lista.txt"
            lineas = [
                "file '" + Path(v["archivo"]).as_posix().replace("'", r"'\''") + "'"
                for v in (v1, v2)
            ]
            lista.write_text("\n".join(lineas), encoding="utf-8")
            self.ffmpeg(
                ["-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy",
                 "-movflags", "+faststart", str(destino)],
                v1["duracion"] + v2["duracion"],
            )
        else:
            self.estado("Uniendo vídeos (recodificando, puede tardar)...")
            self.log(
                f"Los vídeos son distintos ({v1['ancho']}x{v1['alto']} vs "
                f"{v2['ancho']}x{v2['alto']}): se ajusta el 2 al formato del 1."
            )
            self.unir_recodificando(v1, v2, destino)
        self.avance(1)
        self.cola.put(("fin", (True, destino)))

    # ---- descarga con yt-dlp
    def descargar(self, enlace, temp, base, prefijo):
        trabajo = self

        class Registro:
            def debug(self, msg):
                pass

            def info(self, msg):
                pass

            def warning(self, msg):
                if "JavaScript runtime" in msg:
                    msg = ("Este PC no tiene Node ni Deno instalados: algunas webs pueden ofrecer "
                           "menos calidades. Para máxima compatibilidad instala Deno (deno.com).")
                trabajo.log("Aviso: " + ANSI.sub("", msg))

            def error(self, msg):
                # El error final llega como excepción; aquí se guardan los internos
                # (p. ej. «Unexpected error» de una conexión) para poder diagnosticarlos.
                limpio = ANSI.sub("", msg).replace("ERROR: ", "").strip()
                anotar("yt-dlp: " + limpio)
                if "Unexpected error" in limpio:
                    trabajo.log("Detalle técnico: " + limpio.split(";")[0][:300])

        anunciado = []

        def gancho(d):
            if self.cancelado.is_set():
                raise Cancelado
            if not anunciado:
                anunciado.append(True)
                self.log(f"{prefijo}{d.get('info_dict', {}).get('title', enlace)}")
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                hecho = d.get("downloaded_bytes") or 0
                if total:
                    self.avance(hecho / total)
                    vel = ANSI.sub("", d.get("_speed_str", "")).strip()
                    self.estado(f"{prefijo}descargando {hecho / total:.0%}  ({vel})")

        opciones = {
            "format": FORMATO,
            "merge_output_format": "mp4",
            "ffmpeg_location": FFMPEG,
            "outtmpl": str(temp / f"{base}.%(ext)s"),
            "noplaylist": True,
            "playlist_items": "1",
            "progress_hooks": [gancho],
            "logger": Registro(),
            "quiet": True,
            "noprogress": True,
            "js_runtimes": MOTORES_JS,
            "cachedir": str(DATOS / "cache"),
        }
        self.estado(f"{prefijo}obteniendo información...")
        intentos = 3
        for intento in range(1, intentos + 1):
            try:
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    info = ydl.extract_info(enlace, download=True)
                break
            except yt_dlp.utils.DownloadError as e:
                # Fallos puntuales que se arreglan solos al reintentar: el servidor rechaza
                # la descarga (403) o falla la conexión interna (p. ej. en el primer arranque).
                texto = str(e)
                rechazo = "403" in texto
                conexion = "Unable to handle request" in texto or "Unexpected error" in texto
                if self.cancelado.is_set() or not (rechazo or conexion) or intento == intentos:
                    raise
                motivo = "El servidor rechazó la descarga" if rechazo else "Falló la conexión"
                self.log(f"{prefijo}{motivo}, reintentando ({intento + 1}/{intentos})...")
                for resto in temp.glob(f"{base}*"):
                    resto.unlink(missing_ok=True)
                time.sleep(2 * intento)
        if self.cancelado.is_set():
            raise Cancelado
        if info and info.get("entries"):  # enlace de lista: solo el primer vídeo
            info = next((e for e in info["entries"] if e), None)
        if not info or not info.get("requested_downloads"):
            raise RuntimeError("No se pudo descargar el vídeo (¿es un enlace válido?).")
        descarga = info["requested_downloads"][0]
        archivo = descarga["filepath"]
        vcodec = descarga.get("vcodec") or info.get("vcodec") or ""
        acodec = descarga.get("acodec") or info.get("acodec") or ""
        self.log(
            f"   {info.get('width')}x{info.get('height')}, {info.get('fps') or '?'} fps, "
            f"{vcodec.split('.')[0]}/{acodec.split('.')[0]}"
        )
        self.avance(1)
        return {
            "archivo": archivo,
            "title": info.get("title") or "video",
            "ancho": info.get("width"),
            "alto": info.get("height"),
            "fps": info.get("fps"),
            "vcodec": vcodec,
            "acodec": acodec,
            "asr": info.get("asr"),
            "duracion": float(info.get("duration") or 0),
            "tiene_audio": acodec not in ("", "none"),
            "h264_aac": vcodec.startswith("avc1") and acodec.startswith("mp4a")
            and archivo.lower().endswith(".mp4"),
        }

    @staticmethod
    def compatibles(a, b):
        mismo = all(a[k] == b[k] for k in ("ancho", "alto", "vcodec", "acodec", "asr"))
        fps_igual = round(a["fps"] or 0) == round(b["fps"] or 0)
        return mismo and fps_igual and a["h264_aac"] and b["h264_aac"]

    def unir_recodificando(self, v1, v2, destino):
        ancho, alto = par(v1["ancho"]) or 1280, par(v1["alto"]) or 720
        fps = v1["fps"] or 30
        entradas, filtros, pares = [], [], ""
        siguiente = 0
        for i, v in enumerate((v1, v2)):
            entradas += ["-i", v["archivo"]]
            idx_v = siguiente
            siguiente += 1
            if v["tiene_audio"]:
                fuente_audio = f"[{idx_v}:a]"
            else:  # silencio para que la unión no falle
                entradas += ["-f", "lavfi", "-t", str(v["duracion"] or 1),
                             "-i", "anullsrc=r=48000:cl=stereo"]
                fuente_audio = f"[{siguiente}:a]"
                siguiente += 1
            filtros.append(
                f"[{idx_v}:v]scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
                f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]"
            )
            filtros.append(
                f"{fuente_audio}aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            )
            pares += f"[v{i}][a{i}]"
        filtros.append(f"{pares}concat=n=2:v=1:a=1[v][a]")
        self.ffmpeg(
            entradas + [
                "-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destino),
            ],
            v1["duracion"] + v2["duracion"],
        )

    # ---- ffmpeg con progreso
    def ffmpeg(self, argumentos, duracion_total):
        comando = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                   "-progress", "pipe:1", "-nostats", *argumentos]
        self.proceso = subprocess.Popen(
            comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=SIN_VENTANA,
        )
        errores = []
        lector = threading.Thread(
            target=lambda: errores.extend(self.proceso.stderr), daemon=True
        )
        lector.start()
        for linea in self.proceso.stdout:
            clave, _, valor = linea.strip().partition("=")
            if clave in ("out_time_us", "out_time_ms") and duracion_total and valor.isdigit():
                self.avance(int(valor) / 1_000_000 / duracion_total)
        self.proceso.wait()
        lector.join(timeout=2)
        if self.cancelado.is_set():
            Path(argumentos[-1]).unlink(missing_ok=True)
            raise Cancelado
        if self.proceso.returncode != 0:
            Path(argumentos[-1]).unlink(missing_ok=True)
            detalle = "".join(errores[-5:]).strip() or f"código {self.proceso.returncode}"
            raise RuntimeError(f"ffmpeg falló: {detalle}")


# --------------------------------------------------------------------------- interfaz

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.minsize(640, 540)
        self.geometry("740x600")
        self.cola = queue.Queue()
        self.trabajo = None
        self.dialogo = None
        self.hilo = None
        self.actualizando = False
        self.config_datos = leer_config()
        self.carpeta = Path(self.config_datos.get("carpeta") or carpeta_por_defecto())

        icono = next((p for p in (RECURSOS / "icono.ico", DIR_PROGRAMA / "empaquetado" / "icono.ico")
                      if p.exists()), None)
        if icono:
            try:
                self.iconbitmap(default=str(icono))
            except tk.TclError:
                pass

        estilo = ttk.Style(self)
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure("Grande.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 15, "bold"))
        estilo.configure("Nota.TLabel", foreground="#555")
        estilo.configure("Aviso.TLabel", foreground="#0a6b2b")
        estilo.configure("Exito.TLabel", foreground="#0a6b2b", font=("Segoe UI", 10, "bold"))
        estilo.configure("Fallo.TLabel", foreground="#b42318", font=("Segoe UI", 10, "bold"))
        estilo.configure("Normal.TLabel")

        marco = ttk.Frame(self, padding=16)
        marco.pack(fill="both", expand=True)
        marco.columnconfigure(0, weight=1)
        fila = 0

        ttk.Label(marco, text=APP, style="Titulo.TLabel").grid(row=fila, column=0, sticky="w")
        fila += 1

        # ---- selector de modo (por defecto: un solo vídeo)
        self.modo = tk.StringVar(value="uno")
        selector = ttk.Frame(marco)
        selector.grid(row=fila, column=0, sticky="w", pady=(8, 10))
        ttk.Label(selector, text="¿Qué quieres hacer?").pack(side="left", padx=(0, 10))
        self.radios = [
            ttk.Radiobutton(selector, text="Descargar un vídeo", value="uno",
                            variable=self.modo, command=self.cambiar_modo),
            ttk.Radiobutton(selector, text="Unir dos vídeos", value="dos",
                            variable=self.modo, command=self.cambiar_modo),
        ]
        for r in self.radios:
            r.pack(side="left", padx=(0, 12))
        fila += 1

        # ---- enlaces
        self.enlace1 = tk.StringVar()
        self.enlace2 = tk.StringVar()
        self.controles = list(self.radios)

        self.etiqueta1 = ttk.Label(marco)
        self.etiqueta1.grid(row=fila, column=0, sticky="w")
        fila += 1
        self.fila_enlace(marco, fila, self.enlace1)
        fila += 1

        self.bloque2 = ttk.Frame(marco)
        self.bloque2.grid(row=fila, column=0, sticky="ew")
        self.bloque2.columnconfigure(0, weight=1)
        ttk.Label(self.bloque2, text="Enlace 2 (va después del 1)").grid(row=0, column=0, sticky="w")
        self.fila_enlace(self.bloque2, 1, self.enlace2)
        fila += 1

        self.texto_modo = ttk.Label(marco, style="Nota.TLabel")
        self.texto_modo.grid(row=fila, column=0, sticky="w")
        fila += 1

        # ---- carpeta de destino
        destino = ttk.Frame(marco)
        destino.grid(row=fila, column=0, sticky="ew", pady=(10, 0))
        destino.columnconfigure(1, weight=1)
        ttk.Label(destino, text="Guardar en:").grid(row=0, column=0, sticky="w")
        self.texto_carpeta = ttk.Label(destino, style="Nota.TLabel")
        self.texto_carpeta.grid(row=0, column=1, sticky="w", padx=6)
        self.boton_carpeta = ttk.Button(destino, text="Cambiar...", command=self.elegir_carpeta)
        self.boton_carpeta.grid(row=0, column=2)
        ttk.Button(destino, text="Abrir carpeta", command=self.abrir_carpeta).grid(
            row=0, column=3, padx=(6, 0))
        self.controles.append(self.boton_carpeta)
        fila += 1

        # ---- botones principales
        botones = ttk.Frame(marco)
        botones.grid(row=fila, column=0, sticky="ew", pady=(12, 10))
        self.boton = ttk.Button(botones, text="Descargar", style="Grande.TButton",
                                command=self.iniciar)
        self.boton.pack(side="left")
        self.boton_limpiar = ttk.Button(botones, text="Limpiar", command=self.limpiar)
        self.boton_limpiar.pack(side="left", padx=8)
        self.controles.append(self.boton_limpiar)
        self.boton_cerrar = ttk.Button(botones, text="Cerrar", command=self.cerrar)
        self.boton_cerrar.pack(side="right")
        fila += 1

        self.barra = ttk.Progressbar(marco, maximum=100)
        self.barra.grid(row=fila, column=0, sticky="ew")
        fila += 1
        self.estado = ttk.Label(marco, text="Listo.")
        self.estado.grid(row=fila, column=0, sticky="w", pady=(4, 8))
        fila += 1

        registro = ttk.Frame(marco)
        registro.grid(row=fila, column=0, sticky="nsew")
        marco.rowconfigure(fila, weight=1)
        self.texto = tk.Text(registro, height=7, wrap="word", state="disabled",
                             font=("Consolas", 9), relief="solid", borderwidth=1)
        barra_texto = ttk.Scrollbar(registro, command=self.texto.yview)
        self.texto.configure(yscrollcommand=barra_texto.set)
        self.texto.pack(side="left", fill="both", expand=True)
        barra_texto.pack(side="right", fill="y")
        fila += 1

        # ---- pie: versión del motor y actualización
        pie = ttk.Frame(marco)
        pie.grid(row=fila, column=0, sticky="ew", pady=(10, 0))
        self.texto_version = ttk.Label(pie, style="Nota.TLabel",
                                       text=f"Motor de descarga: yt-dlp {yt_dlp.version.__version__}")
        self.texto_version.pack(side="left")
        self.boton_actualizar = ttk.Button(pie, text="Buscar actualización",
                                           command=self.actualizar)
        self.boton_actualizar.pack(side="right")
        ttk.Button(pie, text="Aviso legal", command=lambda: webbrowser.open(URL_LEGAL)).pack(
            side="right", padx=(0, 8))
        self.aviso_version = ttk.Label(pie, style="Aviso.TLabel")
        self.aviso_version.pack(side="right", padx=8)

        self.bind("<Return>", lambda _: self.iniciar())
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.cambiar_modo()
        self.mostrar_carpeta()
        self.after(100, self.revisar_cola)
        self.after(150, self.comprobar_terminos)
        threading.Thread(target=self.comprobar_version, daemon=True).start()

    # ---- términos de uso (se aceptan una vez)
    def comprobar_terminos(self):
        if self.config_datos.get("terminos") == TERMINOS_VERSION:
            return
        d = tk.Toplevel(self)
        self.dialogo_terminos = d
        d.title("Antes de empezar: términos de uso")
        d.transient(self)
        d.resizable(False, False)
        marco = ttk.Frame(d, padding=(26, 22))
        marco.pack(fill="both", expand=True)
        ttk.Label(marco, text="Términos de uso", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        texto = (
            "Este programa es un proyecto personal, gratuito y sin ánimo de lucro. "
            "Se ofrece «tal cual», sin garantías de ningún tipo.\n\n"
            "Úsalo solo con contenido que tengas derecho a descargar:\n"
            "   •  tus propios vídeos;\n"
            "   •  contenido de dominio público o con una licencia que lo permita;\n"
            "   •  contenido para el que tengas permiso de su titular.\n\n"
            "No lo uses para descargar contenido protegido sin permiso ni para eludir "
            "protecciones o restricciones de acceso. Respeta las condiciones de cada web.\n\n"
            "Eres el único responsable del uso que hagas del programa y de lo que descargues."
        )
        ttk.Label(marco, text=texto, wraplength=470, justify="left").pack(anchor="w", pady=(10, 8))
        local = DIR_PROGRAMA / "TERMINOS.txt"
        ttk.Button(marco, text="Leer los términos completos",
                   command=lambda: os.startfile(local) if local.exists() else webbrowser.open(URL_LEGAL)
                   ).pack(anchor="w", pady=(0, 16))
        botones = ttk.Frame(marco)
        botones.pack(fill="x")

        def aceptar():
            self.config_datos["terminos"] = TERMINOS_VERSION
            guardar_config(self.config_datos)
            d.grab_release()
            d.destroy()
            self.dialogo_terminos = None
            self.entrada1.focus_set()

        def rechazar():
            self.destroy()

        ttk.Button(botones, text="Acepto los términos", style="Grande.TButton", command=aceptar).pack(side="right")
        ttk.Button(botones, text="No acepto (salir)", command=rechazar).pack(side="right", padx=8)
        d.protocol("WM_DELETE_WINDOW", rechazar)
        d.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - d.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + max(20, (self.winfo_height() - d.winfo_reqheight()) // 3)
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.grab_set()
        d.focus_force()

    def fila_enlace(self, padre, fila, var):
        entrada = ttk.Entry(padre, textvariable=var, font=("Segoe UI", 10))
        entrada.grid(row=fila, column=0, sticky="ew", pady=(2, 10), ipady=3)
        boton = ttk.Button(padre, text="Pegar", command=lambda: self.pegar(var))
        boton.grid(row=fila, column=1, padx=(8, 0), pady=(2, 10))
        padre.columnconfigure(0, weight=1)
        self.controles += [entrada, boton]
        var.trace_add("write", lambda *_: self.actualizar_texto_modo())
        if var is self.enlace1:
            self.entrada1 = entrada

    # ---- modo
    def cambiar_modo(self):
        if self.modo.get() == "dos":
            self.etiqueta1.configure(text="Enlace 1 (va primero)")
            self.bloque2.grid()
        else:
            self.etiqueta1.configure(text="Enlace del vídeo")
            self.bloque2.grid_remove()
        self.actualizar_texto_modo()
        self.entrada1.focus_set()

    def actualizar_texto_modo(self):
        uno, dos = self.enlace1.get().strip(), self.enlace2.get().strip()
        if self.modo.get() == "uno":
            texto = "Se descargará el vídeo en MP4." if uno else "Pega el enlace del vídeo."
        elif uno and dos:
            texto = "Se descargarán los 2 vídeos y se unirán en un solo MP4 (primero el 1)."
        else:
            texto = "Pega los dos enlaces para unirlos."
        self.texto_modo.configure(text=texto)

    def enlaces(self):
        uno, dos = self.enlace1.get().strip(), self.enlace2.get().strip()
        return [uno] if self.modo.get() == "uno" else [uno, dos]

    # ---- acciones
    def pegar(self, var):
        try:
            var.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def limpiar(self):
        self.enlace1.set("")
        self.enlace2.set("")
        self.entrada1.focus_set()

    def mostrar_carpeta(self):
        texto = str(self.carpeta)
        if len(texto) > 60:
            texto = "..." + texto[-57:]
        self.texto_carpeta.configure(text=texto)

    def elegir_carpeta(self):
        elegida = filedialog.askdirectory(title="Elige dónde guardar los vídeos",
                                          initialdir=str(self.carpeta if self.carpeta.exists()
                                                         else Path.home()))
        if elegida:
            self.carpeta = Path(elegida)
            self.config_datos["carpeta"] = str(self.carpeta)
            guardar_config(self.config_datos)
            self.mostrar_carpeta()

    def abrir_carpeta(self):
        try:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            os.startfile(self.carpeta)
        except OSError as e:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n\n{e}")

    def iniciar(self):
        if self.trabajo:
            self.trabajo.cancelar()
            self.boton.configure(state="disabled", text="Cancelando...")
            return
        if self.actualizando:
            return
        enlaces = self.enlaces()
        if not all(enlaces):
            if self.modo.get() == "uno":
                messagebox.showwarning("Falta el enlace", "Pega el enlace del vídeo.")
            else:
                messagebox.showwarning("Faltan enlaces",
                                       "Para unir vídeos necesitas pegar los dos enlaces.")
            return
        for enlace in enlaces:
            if not re.match(r"https?://", enlace):
                messagebox.showwarning("Enlace no válido",
                                       f"Esto no parece un enlace:\n\n{enlace}")
                return
        self.bloquear(True)
        self.barra["value"] = 0
        self.estado.configure(text="Empezando...", style="Normal.TLabel")
        self.escribir("—" * 40)
        self.trabajo = Trabajo(enlaces, self.carpeta, self.cola)
        self.hilo = threading.Thread(target=self.trabajo.ejecutar, daemon=True)
        self.hilo.start()

    def bloquear(self, ocupado):
        for w in self.controles + [self.boton_actualizar]:
            w.configure(state="disabled" if ocupado else "normal")
        self.boton.configure(state="normal", text="Cancelar" if ocupado else "Descargar")

    def escribir(self, texto):
        self.texto.configure(state="normal")
        self.texto.insert("end", texto + "\n")
        self.texto.see("end")
        self.texto.configure(state="disabled")

    def revisar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "log":
                    self.escribir(dato)
                elif tipo == "estado":
                    self.estado.configure(text=dato)
                elif tipo == "progreso":
                    self.barra["value"] = dato
                elif tipo == "fin":
                    self.terminar(*dato)
                elif tipo == "version":
                    self.aviso_version.configure(text=f"Nueva versión disponible: {dato}")
                elif tipo == "actualizado":
                    self.fin_actualizacion(*dato)
        except queue.Empty:
            pass
        self.after(100, self.revisar_cola)

    def terminar(self, ok, dato):
        self.trabajo = None
        self.bloquear(False)
        if ok:
            self.barra["value"] = 100
            self.estado.configure(text=f"✔ Finalizado: {Path(dato).name}", style="Exito.TLabel")
            self.escribir(f"Guardado: {dato}")
            self.mostrar_resultado(True, "¡Descarga finalizada!",
                                   f"El vídeo se ha guardado como:\n{Path(dato).name}", Path(dato))
        else:
            self.barra["value"] = 0
            self.escribir(f"Error: {dato}")
            if dato == "Operación cancelada.":
                self.estado.configure(text="Cancelado.", style="Normal.TLabel")
            else:
                self.estado.configure(text="✖ No se pudo completar.", style="Fallo.TLabel")
                self.mostrar_resultado(False, "No se pudo completar", dato)

    def mostrar_resultado(self, ok, titulo, detalle, archivo=None):
        """Ventana de aviso al terminar (con botones para ver el vídeo y cerrar)."""
        # Traer el programa al frente y avisar con un sonido.
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.bell()

        d = tk.Toplevel(self)
        self.dialogo = d
        d.title(titulo)
        d.transient(self)
        d.resizable(False, False)
        marco = ttk.Frame(d, padding=(24, 20))
        marco.pack(fill="both", expand=True)
        tk.Label(marco, text="✔" if ok else "✖", font=("Segoe UI", 34, "bold"),
                 fg="#0a6b2b" if ok else "#b42318").grid(row=0, column=0, rowspan=2,
                                                          sticky="n", padx=(0, 16))
        ttk.Label(marco, text=titulo, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=1, sticky="w")
        ttk.Label(marco, text=detalle, wraplength=380, justify="left").grid(
            row=1, column=1, sticky="w", pady=(6, 16))

        botones = ttk.Frame(marco)
        botones.grid(row=2, column=0, columnspan=2, sticky="e")

        def cerrar_dialogo(_=None):
            d.grab_release()
            d.destroy()
            self.dialogo = None

        if archivo:
            ttk.Button(botones, text="▶ Reproducir vídeo",
                       command=lambda: (os.startfile(archivo), cerrar_dialogo())).pack(
                side="left", padx=(0, 8))
            ttk.Button(botones, text="Abrir carpeta",
                       command=lambda: (subprocess.Popen(f'explorer /select,"{archivo}"'),
                                        cerrar_dialogo())).pack(side="left", padx=(0, 8))
        elif REGISTRO.exists():
            ttk.Button(botones, text="Ver detalles",
                       command=lambda: os.startfile(REGISTRO)).pack(side="left", padx=(0, 8))
        aceptar = ttk.Button(botones, text="Aceptar", command=cerrar_dialogo)
        aceptar.pack(side="left")
        d.bind("<Return>", cerrar_dialogo)
        d.bind("<Escape>", cerrar_dialogo)
        d.protocol("WM_DELETE_WINDOW", cerrar_dialogo)

        d.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - d.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - d.winfo_reqheight()) // 3
        d.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        d.grab_set()
        aceptar.focus_force()

    # ---- actualización
    def comprobar_version(self):
        try:
            ultima, _ = ultima_version()
            if version_tupla(ultima) > version_tupla(yt_dlp.version.__version__):
                self.cola.put(("version", ultima))
        except Exception:  # noqa: BLE001 - sin internet, etc.: no pasa nada
            pass

    def actualizar(self):
        if self.trabajo or self.actualizando:
            return
        self.actualizando = True
        self.bloquear(True)
        self.boton.configure(state="disabled", text="Descargar")
        self.estado.configure(text="Buscando actualización...")

        def tarea():
            try:
                ultima, datos = ultima_version()
                if version_tupla(ultima) <= version_tupla(yt_dlp.version.__version__):
                    self.cola.put(("actualizado", (None, ultima)))
                    return
                instalar_actualizacion(datos)
                self.cola.put(("actualizado", (True, ultima)))
            except Exception as e:  # noqa: BLE001
                self.cola.put(("actualizado", (False, str(e))))

        threading.Thread(target=tarea, daemon=True).start()

    def fin_actualizacion(self, resultado, dato):
        self.actualizando = False
        self.bloquear(False)
        self.estado.configure(text="Listo.")
        if resultado is None:
            self.aviso_version.configure(text="")
            messagebox.showinfo("Actualización", f"Ya tienes la última versión ({dato}).")
        elif resultado:
            self.escribir(f"Motor de descarga actualizado a yt-dlp {dato}.")
            if messagebox.askyesno("Actualización",
                                   f"Actualizado a yt-dlp {dato}.\n\n"
                                   "Hay que reiniciar el programa para usarlo. ¿Reiniciar ahora?"):
                reiniciar()
                self.destroy()
        else:
            messagebox.showerror("Actualización", f"No se pudo actualizar:\n\n{dato}")

    def cerrar(self):
        if self.trabajo:
            if not messagebox.askyesno("Salir", "Hay una descarga en curso. ¿Cancelar y salir?"):
                return
            self.trabajo.cancelar()
            self.estado.configure(text="Cancelando y cerrando...", style="Normal.TLabel")
            self.boton_cerrar.configure(state="disabled")
            self.esperar_y_cerrar(time.time() + 8)
            return
        self.destroy()

    def esperar_y_cerrar(self, limite):
        """Deja que el trabajo cancelado borre sus temporales antes de salir."""
        if self.hilo and self.hilo.is_alive() and time.time() < limite:
            self.after(150, lambda: self.esperar_y_cerrar(limite))
        else:
            self.destroy()


def autoprueba(argumentos):
    """Modo sin ventana para comprobar el .exe:
    --autoprueba CARPETA ENLACE [ENLACE2]  -> escribe CARPETA/resultado.txt"""
    carpeta, enlaces = Path(argumentos[0]), argumentos[1:3]
    cola = queue.Queue()
    Trabajo(enlaces, carpeta, cola).ejecutar()
    lineas = [f"yt-dlp {yt_dlp.version.__version__} | motores JS {MOTORES_JS} | ffmpeg {FFMPEG}"]
    while not cola.empty():
        tipo, dato = cola.get()
        if tipo in ("log", "fin"):
            lineas.append(f"[{tipo}] {dato}")
    (carpeta / "resultado.txt").write_text("\n".join(lineas), encoding="utf-8")


def autoprueba_ventana(argumentos):
    """--autoprueba-ventana CARPETA ENLACE: abre la ventana real (con la comprobación de
    versión en segundo plano) y pulsa «Descargar» al instante, como en el primer arranque."""
    carpeta, enlace = Path(argumentos[0]), argumentos[1]
    app = App()
    app.config_datos["terminos"] = TERMINOS_VERSION  # solo en memoria, para esta prueba
    app.carpeta = carpeta
    app.enlace1.set(enlace)
    terminar_original = app.terminar

    def terminar(ok, dato):
        terminar_original(ok, dato)
        registro = app.texto.get("1.0", "end").strip()
        (carpeta / "resultado.txt").write_text(f"[fin] {(ok, str(dato))}\n{registro}", encoding="utf-8")
        app.after(400, app.destroy)

    app.terminar = terminar
    app.after(10, app.iniciar)
    app.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "--autoprueba":
        autoprueba(sys.argv[2:])
    elif len(sys.argv) > 3 and sys.argv[1] == "--autoprueba-ventana":
        autoprueba_ventana(sys.argv[2:])
    else:
        App().mainloop()
