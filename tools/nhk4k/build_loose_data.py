#!/usr/bin/env python3
"""Build a complete loose_4k replacement set from original game data.

Pipeline: extract all bitmaps/animation frames from the BLB archives,
upscale them by the given factor (Lanczos placeholder quality for now; an
AI upscaler can replace the upscale step later), and convert all Smacker
videos to Theora .ogv at the same factor via ffmpeg. Output layout matches
the engine contract exactly:

    <out>/images/XXXXXXXX.png            (stills)
    <out>/images/XXXXXXXX-000.png ...    (animation frames)
    <out>/videos/XXXXXXXX.ogv           (cutscenes)

The n4k engine has NO fallback for missing videos (it crashes) and draws
unreplaced bitmaps unscaled, so a complete set is required for factors > 1.

Usage:
  python build_loose_data.py [--factor 4] [--data-dir DIR] [--out DIR]
                             [--skip-images] [--skip-videos] [--jobs N]
                             [--ffmpeg PATH] [--video-quality 7]
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = r"C:\Users\apa\neverhood4k\image\DATA"
DEFAULT_OUT = os.path.join(DEFAULT_DATA, "loose_4k")
DEFAULT_WORK = r"C:\Users\apa\neverhood4k\extracted"
DEFAULT_FFMPEG = r"C:\msys64\mingw64\bin\ffmpeg.exe"

REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def guard_outside_repo(path):
    if os.path.abspath(path).lower().startswith(REPO_ROOT.lower()):
        sys.exit("refusing to write game-derived data inside the repo: " + path)


def upscale_one(src, dst, factor):
    from PIL import Image
    im = Image.open(src)
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (
        im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    im.resize((im.width * factor, im.height * factor),
              Image.LANCZOS).save(dst)
    return dst


FFMPEG2THEORA = r"C:\Users\apa\neverhood4k\aitools\ffmpeg2theora.exe"


def convert_video(ffmpeg, src, dst, factor, quality):
    # MSYS2's libtheora 1.2.0 encoder emits corrupt bitstreams on multi-frame
    # streams (unpack_block_qpis decode errors), so encode video through the
    # standalone ffmpeg2theora (libtheora 1.2.0alpha Ptalarbvorm) via a
    # yuv4mpeg pipe, then mux audio (if any) with ffmpeg stream-copy.
    # Fullscreen cutscenes are 320x240 sources the original engine doubled to
    # 640x480; the n4k engine blits replacement frames 1:1, so they need 2x
    # the factor. Other videos play at native game-space size -> plain factor.
    probe0 = subprocess.run([ffmpeg, "-i", src], capture_output=True, text=True)
    eff = factor * 2 if " 320x240" in probe0.stderr else factor
    tmp = dst + ".video.ogv"
    dec = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-i", src,
         "-vf", f"scale=iw*{eff}:ih*{eff}:flags=lanczos,format=yuv420p",
         "-f", "yuv4mpegpipe", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.run(
        [FFMPEG2THEORA, "-", "-v", str(quality), "--no-skeleton", "-o", tmp],
        stdin=dec.stdout, capture_output=True, text=True)
    dec.stdout.close()
    dec.wait()
    if enc.returncode != 0 or dec.returncode != 0 or not os.path.isfile(tmp):
        raise RuntimeError(f"{os.path.basename(src)}: {enc.stderr.strip()[-200:]}")
    probe = subprocess.run([ffmpeg, "-i", src], capture_output=True, text=True)
    if "Audio:" in probe.stderr:
        mux = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", tmp, "-i", src,
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "libvorbis", "-q:a", "4", dst],
            capture_output=True, text=True)
        os.remove(tmp)
        if mux.returncode != 0:
            raise RuntimeError(f"{os.path.basename(src)} mux: {mux.stderr.strip()[-200:]}")
    else:
        os.replace(tmp, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", type=int, default=4)
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--work", default=DEFAULT_WORK,
                    help="scratch dir for extracted originals")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--skip-videos", action="store_true")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    ap.add_argument("--video-quality", type=int, default=7)
    args = ap.parse_args()

    guard_outside_repo(args.out)
    guard_outside_repo(args.work)
    py = sys.executable
    img_out = os.path.join(args.out, "images")
    vid_out = os.path.join(args.out, "videos")
    os.makedirs(img_out, exist_ok=True)
    os.makedirs(vid_out, exist_ok=True)

    def extract(rtype):
        subprocess.run(
            [py, os.path.join(HERE, "extract_assets.py"),
             "--data-dir", args.data_dir, "--out", args.work,
             "--type", rtype],
            check=True)

    if not args.skip_images:
        print("== extracting bitmaps and animations ==")
        extract("bitmap")
        extract("animation")
        srcs = []
        for sub in ("bitmap", "animation"):
            d = os.path.join(args.work, sub)
            if os.path.isdir(d):
                srcs += [os.path.join(d, f) for f in os.listdir(d)
                         if f.lower().endswith(".png")]
        print(f"== upscaling {len(srcs)} images x{args.factor} ==")
        done = err = 0
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(upscale_one, s,
                              os.path.join(img_out, os.path.basename(s)),
                              args.factor): s for s in srcs}
            for f in as_completed(futs):
                try:
                    f.result()
                    done += 1
                except Exception as e:
                    err += 1
                    print("ERR", futs[f], e)
                if (done + err) % 500 == 0:
                    print(f"  {done + err}/{len(srcs)}")
        print(f"images: {done} ok, {err} errors")

    if not args.skip_videos:
        if not os.path.isfile(args.ffmpeg):
            sys.exit("ffmpeg not found: " + args.ffmpeg)
        print("== extracting videos ==")
        extract("video")
        vdir = os.path.join(args.work, "video")
        smks = [os.path.join(vdir, f) for f in os.listdir(vdir)
                if f.lower().endswith(".smk")]
        todo = [s for s in smks if not os.path.isfile(
            os.path.join(vid_out, os.path.splitext(os.path.basename(s))[0] + ".ogv"))]
        print(f"== converting {len(todo)} videos (of {len(smks)}) x{args.factor} ==")
        done = err = 0
        with ProcessPoolExecutor(max_workers=max(1, args.jobs // 2)) as ex:
            futs = {ex.submit(convert_video, args.ffmpeg, s,
                              os.path.join(vid_out,
                                           os.path.splitext(os.path.basename(s))[0] + ".ogv"),
                              args.factor, args.video_quality): s for s in todo}
            for f in as_completed(futs):
                try:
                    f.result()
                    done += 1
                except Exception as e:
                    err += 1
                    print("ERR", e)
                if (done + err) % 25 == 0:
                    print(f"  {done + err}/{len(todo)}")
        print(f"videos: {done} ok, {err} errors, {len(smks) - len(todo)} already present")

    print("done ->", args.out)


if __name__ == "__main__":
    main()
