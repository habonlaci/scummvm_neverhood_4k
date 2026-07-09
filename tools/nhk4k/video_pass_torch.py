#!/usr/bin/env python3
"""Full video pass through a torch/spandrel SR model, tuned for throughput:
multiple worker processes (parallel ffmpeg/theora CPU work) each running
batched CUDA inference. Writes to a separate output dir - never touches
existing video sets. Resumable: existing outputs are skipped.

  python video_pass_torch.py --model <weights> --out <dir> [--workers 3]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nhk4k_paths as P

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def worker(job):
    weights, smk, dst, quality, BATCH = job
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    import numpy as np
    import torch
    from PIL import Image
    global _MODEL
    try:
        _MODEL
    except NameError:
        from spandrel import ModelLoader
        # hard-cap this process's VRAM share: allocator raises OOM instead
        # of silently spilling into (slow) shared system memory
        torch.cuda.set_per_process_memory_fraction(0.28, 0)
        _MODEL = ModelLoader().load_from_file(weights).cuda().eval()
        torch.backends.cudnn.benchmark = True
    model = _MODEL

    r = run([P.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "csv=p=0", smk])
    w, h, rate = r.stdout.strip().split(",")[:3]
    w, h = int(w), int(h)
    tw, th = (2560, 1920) if (w, h) == (320, 240) else (w * 4, h * 4)
    num, den = rate.split("/")
    fps = float(num) / float(den)

    with tempfile.TemporaryDirectory(dir=os.path.dirname(dst)) as td:
        fr = os.path.join(td, "f")
        up = os.path.join(td, "u")
        os.makedirs(fr)
        os.makedirs(up)
        r = run([P.FFMPEG, "-y", "-loglevel", "error", "-i", smk,
                 os.path.join(fr, "%05d.png")])
        if r.returncode != 0:
            raise RuntimeError("extract: " + r.stderr[-120:])
        files = sorted(os.listdir(fr))
        def infer(chunk):
            arrs = [np.asarray(Image.open(os.path.join(fr, f)).convert("RGB"))
                    for f in chunk]
            x = torch.from_numpy(np.stack(arrs)).permute(0, 3, 1, 2)
            x = x.float().div(255).cuda()
            y = model(x).clamp(0, 1).mul(255).byte().permute(0, 2, 3, 1).cpu().numpy()
            for f, o in zip(chunk, y):
                Image.fromarray(o, "RGB").save(os.path.join(up, f))

        with torch.inference_mode():
            i = 0
            batch = BATCH
            while i < len(files):
                chunk = files[i:i + batch]
                try:
                    infer(chunk)
                    i += len(chunk)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if batch == 1:
                        raise
                    batch = max(1, batch // 2)
        tmp = os.path.join(td, "v.ogv")
        subprocess.run(
            f'"{P.FFMPEG}" -y -loglevel error -framerate {fps} '
            f'-i "{os.path.join(up, "%05d.png")}" '
            f'-vf "scale={tw}:{th}:flags=lanczos,format=yuv420p" '
            f'-f yuv4mpegpipe - | "{P.FFMPEG2THEORA}" - -v {quality} '
            f'--no-skeleton -o "{tmp}"',
            shell=True, capture_output=True, text=True)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("encode failed")
        final = tmp
        pr = run([P.FFMPEG, "-i", smk])
        if "Audio:" in pr.stderr:
            mx = os.path.join(td, "m.ogv")
            r = run([P.FFMPEG, "-y", "-loglevel", "error", "-i", tmp, "-i", smk,
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "libvorbis", "-q:a", "4", mx])
            if r.returncode != 0:
                raise RuntimeError("mux: " + r.stderr[-120:])
            final = mx
        r = run([P.FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", final])
        if r.stdout.strip() != "yuv420p":
            raise RuntimeError("verify failed: " + r.stdout.strip())
        shutil.move(final, dst)
    return os.path.basename(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--smk-dir", default=os.path.join(P.EXTRACTED, "video"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--quality", type=int, default=8)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--batch", type=int, default=4)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    jobs = []
    for f in sorted(os.listdir(args.smk_dir)):
        if not f.lower().endswith(".smk"):
            continue
        dst = os.path.join(args.out, os.path.splitext(f)[0] + ".ogv")
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            continue
        jobs.append((args.model, os.path.join(args.smk_dir, f), dst,
                     args.quality, args.batch))
    print(f"{len(jobs)} videos to do", flush=True)

    from concurrent.futures import ProcessPoolExecutor, as_completed
    ok = failed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, j): j for j in jobs}
        for fu in as_completed(futs):
            try:
                fu.result()
                ok += 1
            except Exception as e:
                failed += 1
                print(f"FAIL {os.path.basename(futs[fu][1])}: {e}", flush=True)
            if (ok + failed) % 20 == 0:
                print(f"{ok + failed}/{len(jobs)} ok={ok} failed={failed}",
                      flush=True)
    print(f"done ok={ok} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
