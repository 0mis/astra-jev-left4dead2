"""Paths for a separate user-owned installation; no credentials or downloads."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME_DIR = Path(os.environ.get('L4D2_GAME_DIR', r'C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2\left4dead2'))
OCR_MODELS = Path(os.environ.get('L4D2_OCR_MODELS', ROOT/'models/rapidocr'))
WHISPER_MODEL = Path(os.environ.get('L4D2_WHISPER_MODEL', ROOT/'models/faster-whisper'))
PAUSE_REFERENCE = Path(os.environ.get('L4D2_PAUSE_REFERENCE', ROOT/'private/pause-reference.png'))


def ffmpeg_path():
    value = os.environ.get('L4D2_FFMPEG')
    if value:
        return Path(value)
    import imageio_ffmpeg
    return Path(imageio_ffmpeg.get_ffmpeg_exe())
