@echo off
cd /d "%~dp0"
python -c "import yt_dlp, yt_dlp_ejs, imageio_ffmpeg" 2>nul || (
    echo Instalando dependencias, espera un momento...
    python -m pip install --upgrade --user "yt-dlp[default]" imageio-ffmpeg
)
start "" pythonw descargador_gui.py
