"""Single source of truth for default paths used by the nhk4k tools.

Resolution scheme:
  ROOT   env NHK4K_ROOT, else the parent directory of this repo checkout
         (the repo root is two levels up from tools/nhk4k).
  Everything else derives from ROOT, with env overrides where noted.
All defaults can still be overridden per-script via CLI flags.
"""

import os
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))

ROOT = os.environ.get("NHK4K_ROOT") or os.path.dirname(REPO_ROOT)

DATA_DIR = os.environ.get("NHK4K_DATA") or os.path.join(ROOT, "image", "DATA")
EXTRACTED = os.path.join(ROOT, "extracted")
STAGE = os.path.join(ROOT, "stage1x")
AITOOLS = os.path.join(ROOT, "aitools")
MANIFEST = os.path.join(ROOT, "manifest.json")
PALETTE_MAP = os.path.join(ROOT, "palette_map.json")
VIDEO_BACKUP = os.path.join(ROOT, "videos_lanczos_backup")
NEVERHOOD_DAT = os.path.join(ROOT, "scummvm_extras", "neverhood.dat")

_MSYS2_BIN = r"C:\msys64\mingw64\bin"


def _find_tool(env_var, exe, msys2=False):
    """env override, else (aitools copy unless msys2), else PATH, else default."""
    override = os.environ.get(env_var)
    if override:
        return override
    if msys2:
        return shutil.which(exe) or os.path.join(_MSYS2_BIN, exe)
    local = os.path.join(AITOOLS, exe)
    if os.path.isfile(local):
        return local
    return shutil.which(exe) or local


FFMPEG = _find_tool("NHK4K_FFMPEG", "ffmpeg.exe", msys2=True)
FFPROBE = _find_tool("NHK4K_FFPROBE", "ffprobe.exe", msys2=True)
FFMPEG2THEORA = _find_tool("NHK4K_FFMPEG2THEORA", "ffmpeg2theora.exe")
REALESRGAN_NCNN = _find_tool("NHK4K_REALESRGAN", "realesrgan-ncnn-vulkan.exe")
