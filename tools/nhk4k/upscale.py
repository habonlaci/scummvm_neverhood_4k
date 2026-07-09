#!/usr/bin/env python3
"""One-command asset pipeline for The Neverhood 4K.

    python upscale.py --model anime            # full pipeline
    python upscale.py --model dat --skip-videos
    python upscale.py --list-models

Steps (each skipped automatically when its output already exists,
--force re-runs everything):
  1. coordinate coverage check (informational - engine code ships 100%)
  2. extract bitmaps/animations/videos from the BLBs
  3. re-bake palettes for bitmaps without embedded palettes (1x staging)
  4. AI-upscale images with the chosen model -> loose_<set>/images
  5. AI-upscale videos (animevideov3/ncnn) or Lanczos fallback ->
     shared videos folder (junctioned into every set)
  6. write/refresh the loose set folder + videos junction

Model weights are auto-downloaded on first use.
"""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\Users\apa\neverhood4k"
DATA = os.path.join(ROOT, "image", "DATA")
EXTRACTED = os.path.join(ROOT, "extracted")
STAGE = os.path.join(ROOT, "stage1x")
AITOOLS = os.path.join(ROOT, "aitools")
FACTOR = 4

HF = "https://huggingface.co/{repo}/resolve/main/{file}"
GH_ESRGAN = "https://github.com/xinntao/Real-ESRGAN/releases/download/{tag}/{file}"

# name -> (weights filename, download url, note)
MODELS = {
    "anime": ("RealESRGAN_x4plus_anime_6B.pth",
              GH_ESRGAN.format(tag="v0.2.2.4", file="RealESRGAN_x4plus_anime_6B.pth"),
              "clean stylized repaint (current shipped look)"),
    "x4plus": ("RealESRGAN_x4plus.pth",
               GH_ESRGAN.format(tag="v0.1.0", file="RealESRGAN_x4plus.pth"),
               "photo model, smooths clay texture"),
    "animevideo": ("realesr-animevideov3.pth",
                   GH_ESRGAN.format(tag="v0.2.5.0", file="realesr-animevideov3.pth"),
                   "video-tuned, fast"),
    "dat": ("4xNomos8kDAT.safetensors",
            HF.format(repo="Phips/4xNomos8kDAT", file="4xNomos8kDAT.safetensors"),
            "most faithful film/clay texture"),
    "hatl": ("4xNomos8kSCHAT-L.safetensors",
             HF.format(repo="Phips/4xNomos8kSCHAT-L", file="4xNomos8kSCHAT-L.safetensors"),
             "HAT-L, detail with slight speckle"),
    "sc": ("4xNomos8kSC.safetensors",
           HF.format(repo="Phips/4xNomos8kSC", file="4xNomos8kSC.safetensors"),
           "Nomos8k SC (ESRGAN arch)"),
    "ultrasharp": ("4x-UltraSharp.pth",
                   HF.format(repo="Kim2091/UltraSharp", file="4x-UltraSharp.pth"),
                   "sharp, punchy, modding classic"),
    "remacri": ("4x_foolhardy_Remacri.pth",
                HF.format(repo="FacehugmanIII/4x_foolhardy_Remacri",
                          file="4x_foolhardy_Remacri.pth"),
                "texture-rich, can weave dither"),
    "webphoto": ("4xNomosWebPhoto_RealPLKSR.safetensors",
                 HF.format(repo="Phips/4xNomosWebPhoto_RealPLKSR",
                           file="4xNomosWebPhoto_RealPLKSR.safetensors"),
                 "clean but keeps clay structure, fastest"),
    "atd": ("4xNomos2_hq_atd.safetensors",
            HF.format(repo="Phips/4xNomos2_hq_atd", file="4xNomos2_hq_atd.safetensors"),
            "amplifies palette dither (not recommended)"),
    "drct": ("4xNomos2_hq_drct-l.safetensors",
             HF.format(repo="Phips/4xNomos2_hq_drct-l",
                       file="4xNomos2_hq_drct-l.safetensors"),
             "amplifies palette dither (not recommended)"),
    "lanczos": (None, None, "no AI, fast placeholder quality"),
}


def sh(args, **kw):
    print("+", " ".join(str(a) for a in args), flush=True)
    r = subprocess.run(args, **kw)
    if r.returncode != 0:
        sys.exit(f"step failed ({r.returncode})")


def ensure_weights(name):
    fname, url, _ = MODELS[name]
    if fname is None:
        return None
    path = os.path.join(AITOOLS, fname)
    if not os.path.isfile(path) or os.path.getsize(path) < 1 << 20:
        print(f"downloading {fname} ...", flush=True)
        os.makedirs(AITOOLS, exist_ok=True)
        urllib.request.urlretrieve(url, path)
        if os.path.getsize(path) < 1 << 20:
            sys.exit(f"download of {fname} failed (too small) - check {url}")
    return path


def lanczos_dir(src, dst):
    from concurrent.futures import ProcessPoolExecutor
    files = [f for f in os.listdir(src) if f.endswith(".png")]
    with ProcessPoolExecutor() as ex:
        list(ex.map(_lanczos_one, ((src, dst, f) for f in files), chunksize=64))


def _lanczos_one(args):
    from PIL import Image
    src, dst, f = args
    im = Image.open(os.path.join(src, f)).convert("RGBA")
    im.resize((im.width * FACTOR, im.height * FACTOR),
              Image.LANCZOS).save(os.path.join(dst, f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="anime", choices=sorted(MODELS))
    ap.add_argument("--set-name", help="loose set name (default: model name)")
    ap.add_argument("--skip-videos", action="store_true")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-run extraction/staging even if present")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    if args.list_models:
        for k in sorted(MODELS):
            print(f"{k:12} {MODELS[k][2]}")
        return

    set_name = args.set_name or args.model
    loose = os.path.join(DATA, f"loose_{set_name}")
    img_out = os.path.join(loose, "images")
    videos_shared = os.path.join(DATA, "loose_4k", "videos")

    # 1. coordinate coverage (informational; source ships fully wrapped)
    sh([PY, os.path.join(HERE, "coverage_report.py")])

    # 2. extraction
    if args.force or not os.path.isdir(os.path.join(EXTRACTED, "bitmap")):
        sh([PY, os.path.join(HERE, "extract_assets.py"), "--type", "bitmap"])
        sh([PY, os.path.join(HERE, "extract_assets.py"), "--type", "animation"])
    if not args.skip_videos and (
            args.force or not os.path.isdir(os.path.join(EXTRACTED, "video"))):
        sh([PY, os.path.join(HERE, "extract_assets.py"), "--type", "video"])

    # 3. palette staging
    if args.force or not os.path.isdir(STAGE):
        os.makedirs(STAGE, exist_ok=True)
        for sub in ("bitmap", "animation"):
            d = os.path.join(EXTRACTED, sub)
            for f in os.listdir(d):
                shutil.copy2(os.path.join(d, f), STAGE)
        sh([PY, os.path.join(HERE, "palette_map.py")])
        sh([PY, os.path.join(HERE, "rebake_palettes.py"),
            "--factor", "1", "--images", STAGE])

    # 4. images
    if not args.skip_images:
        os.makedirs(img_out, exist_ok=True)
        if args.model == "lanczos":
            lanczos_dir(STAGE, img_out)
        else:
            weights = ensure_weights(args.model)
            sh([PY, os.path.join(HERE, "ai_upscale.py"),
                "--model", weights, "--src", STAGE, "--out", img_out,
                "--resume"])

    # 5. videos (shared across sets)
    if not args.skip_videos:
        sh([PY, os.path.join(HERE, "ai_upscale_videos.py")])

    # 6. junction videos into the set
    vidlink = os.path.join(loose, "videos")
    if not os.path.isdir(vidlink):
        subprocess.run(["cmd", "/c", "mklink", "/J", vidlink, videos_shared])

    print(f"\ndone. Launch with looseDataFolder=loose_{set_name} "
          f"(run_neverhood.cmd {set_name})")


if __name__ == "__main__":
    main()
