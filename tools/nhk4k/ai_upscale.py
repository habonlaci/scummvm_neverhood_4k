#!/usr/bin/env python3
"""Batch AI upscale via PyTorch/CUDA + spandrel.

Upscales every PNG in --src by the model's scale factor into --out.
RGB goes through the model; the alpha channel is upscaled with Lanczos
and reattached (sprite transparency must survive byte-exact edges are
not required, the engine alpha-blends).

Verifies as it goes: any output that is flat (single color) while its
source is not is reported and the run exits nonzero.

Usage:
  python ai_upscale.py --model WEIGHTS --src DIR --out DIR [--resume]
"""

import argparse
import os
import sys

import numpy as np
import torch
from PIL import Image
from spandrel import ModelLoader


def is_flat(im):
    return all(a == b for a, b in im.convert('RGB').getextrema())


def bleed_colors(rgb, alpha, iterations=8):
    """Spread edge colors into fully transparent areas so the SR model
    doesn't smear the (invisible, often garish) under-color into visible
    edge pixels. Pure numpy dilation."""
    rgb = rgb.astype(np.float32)
    known = alpha > 0
    for _ in range(iterations):
        if known.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape, np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            k = np.roll(known, (dy, dx), (0, 1))
            r = np.roll(rgb, (dy, dx), (0, 1))
            if dy == 1:
                k[0, :] = False
            if dy == -1:
                k[-1, :] = False
            if dx == 1:
                k[:, 0] = False
            if dx == -1:
                k[:, -1] = False
            acc += r * k[..., None]
            cnt += k
        fill = ~known & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill, None]
        known |= fill
    return rgb.astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--resume', action='store_true',
                    help='skip files already present in --out')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    model = ModelLoader().load_from_file(args.model).cuda().eval()
    scale = model.scale
    torch.backends.cudnn.benchmark = True

    files = sorted(f for f in os.listdir(args.src) if f.lower().endswith('.png'))
    if args.resume:
        have = set(os.listdir(args.out))
        files = [f for f in files if f not in have]
    print(f'{len(files)} files, scale x{scale}', flush=True)

    problems = 0
    with torch.inference_mode():
        for i, f in enumerate(files):
            im = Image.open(os.path.join(args.src, f))
            rgba = im.convert('RGBA')
            arr = np.asarray(rgba)
            rgb, a = arr[:, :, :3], arr[:, :, 3]
            has_alpha = (a < 255).any()
            if has_alpha:
                # avoid the model smearing hidden under-colors into edges
                rgb = bleed_colors(rgb, a)

            def run(np_img):
                x = torch.from_numpy(np_img.copy()).permute(2, 0, 1)
                x = x.unsqueeze(0).float().div(255).cuda()
                y = model(x)
                return (y.squeeze(0).permute(1, 2, 0).clamp(0, 1)
                        .cpu().numpy() * 255).astype(np.uint8)

            res = Image.fromarray(run(rgb), 'RGB').convert('RGBA')
            if has_alpha:
                # alpha through the model too: smooth, unjagged edges
                a3 = np.repeat(a[:, :, None], 3, axis=2)
                alpha_up = Image.fromarray(run(a3)[:, :, 0], 'L')
            else:
                alpha_up = Image.fromarray(
                    np.full((rgba.height * scale, rgba.width * scale),
                            255, np.uint8), 'L')
            res.putalpha(alpha_up)
            res.save(os.path.join(args.out, f))
            if is_flat(res) and not is_flat(im):
                problems += 1
                print('FLAT OUTPUT:', f, flush=True)
            if (i + 1) % 250 == 0:
                print(f'{i + 1}/{len(files)}', flush=True)
    print(f'done, {problems} problems', flush=True)
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
