#!/usr/bin/env python3
"""AI-upscale all game videos: SMK -> frames -> Real-ESRGAN animevideov3
(ncnn/Vulkan) -> Theora OGV via ffmpeg2theora -> mux original audio.

Fullscreen 320x240 cutscenes are scaled to 2560x1920 (engine blits 1:1,
original engine pixel-doubled them); other videos get 4x.

Each finished video is verified (yuv420p + a probe frame is not flat)
before replacing the existing OGV; the previous OGV is kept under
--backup. On any failure the existing (Lanczos) OGV stays in place.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhk4k_paths as P

FFMPEG = P.FFMPEG
FFPROBE = P.FFPROBE
F2T = P.FFMPEG2THEORA
ESRGAN = P.REALESRGAN_NCNN


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe_wh(path):
    r = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def has_audio(path):
    r = run([FFMPEG, "-i", path])
    return "Audio:" in r.stderr


def convert(smk, dst, quality):
    w, h = probe_wh(smk)
    target_w, target_h = (2560, 1920) if (w, h) == (320, 240) else (w * 4, h * 4)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(dst)) as td:
        fr, up = os.path.join(td, "f"), os.path.join(td, "u")
        os.makedirs(fr)
        os.makedirs(up)
        r = run([FFMPEG, "-y", "-loglevel", "error", "-i", smk,
                 os.path.join(fr, "%05d.png")])
        if r.returncode != 0:
            raise RuntimeError("frame extract: " + r.stderr[-150:])
        n = len(os.listdir(fr))
        r = run([ESRGAN, "-i", fr, "-o", up, "-n", "realesr-animevideov3",
                 "-s", "4", "-f", "png"])
        ups = os.listdir(up)
        if len(ups) != n:
            raise RuntimeError(f"esrgan wrote {len(ups)}/{n} frames")
        # detect the black-output failure mode (device lost writes blanks)
        from PIL import Image
        mid = Image.open(os.path.join(up, sorted(ups)[len(ups) // 2]))
        if all(a == b for a, b in mid.convert("RGB").getextrema()):
            src_mid = Image.open(os.path.join(fr, sorted(os.listdir(fr))[n // 2]))
            if not all(a == b for a, b in src_mid.convert("RGB").getextrema()):
                raise RuntimeError("esrgan produced flat frames (GPU fault)")
        tmp = os.path.join(td, "v.ogv")
        # fps from source
        r = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", smk])
        fps = r.stdout.strip().split("/")
        fps_f = float(fps[0]) / float(fps[1]) if len(fps) == 2 else 15.0
        enc = subprocess.run(
            f'"{FFMPEG}" -y -loglevel error -framerate {fps_f} '
            f'-i "{os.path.join(up, "%05d.png")}" '
            f'-vf "scale={target_w}:{target_h}:flags=lanczos,format=yuv420p" '
            f'-f yuv4mpegpipe - | "{F2T}" - -v {quality} --no-skeleton -o "{tmp}"',
            shell=True, capture_output=True, text=True)
        # ffmpeg2theora segfaults on exit after writing a valid file:
        # judge by output presence/size, not exit code
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("encode failed: " + enc.stderr[-150:])
        final = tmp
        if has_audio(smk):
            mx = os.path.join(td, "m.ogv")
            r = run([FFMPEG, "-y", "-loglevel", "error", "-i", tmp, "-i", smk,
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "libvorbis", "-q:a", "4", mx])
            if r.returncode != 0:
                raise RuntimeError("mux: " + r.stderr[-150:])
            final = mx
        # verify
        r = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", final])
        if r.stdout.strip() != "yuv420p":
            raise RuntimeError("verify: pix_fmt " + r.stdout.strip())
        shutil.move(final, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smk-dir", default=os.path.join(P.EXTRACTED, "video"))
    ap.add_argument("--out-dir",
                    default=os.path.join(P.DATA_DIR, "loose_4k", "videos"))
    ap.add_argument("--backup", default=P.VIDEO_BACKUP)
    ap.add_argument("--quality", type=int, default=8)
    ap.add_argument("--skip", action="append", default=[],
                    help="hashes to leave untouched (e.g. already AI-done)")
    args = ap.parse_args()

    os.makedirs(args.backup, exist_ok=True)
    smks = sorted(f for f in os.listdir(args.smk_dir) if f.lower().endswith(".smk"))
    ok = failed = skipped = 0
    for i, f in enumerate(smks):
        h = os.path.splitext(f)[0]
        if h in args.skip:
            skipped += 1
            continue
        dst = os.path.join(args.out_dir, h + ".ogv")
        bak = os.path.join(args.backup, h + ".ogv")
        if os.path.isfile(bak):  # already AI-converted in a previous run
            skipped += 1
            continue
        try:
            tmp_dst = dst + ".new"
            convert(os.path.join(args.smk_dir, f), tmp_dst, args.quality)
            if os.path.isfile(dst):
                shutil.copy2(dst, bak)
            os.replace(tmp_dst, dst)
            ok += 1
        except Exception as e:
            failed += 1
            print(f"FAIL {h}: {e}", flush=True)
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(smks)} ok={ok} failed={failed}", flush=True)
    print(f"done: ok={ok} failed={failed} skipped={skipped}", flush=True)


if __name__ == "__main__":
    main()
