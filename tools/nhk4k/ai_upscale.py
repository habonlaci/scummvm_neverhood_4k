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
            rgb = np.asarray(rgba)[:, :, :3]
            x = torch.from_numpy(rgb.copy()).permute(2, 0, 1).unsqueeze(0)
            x = x.float().div(255).cuda()
            y = model(x)
            out = (y.squeeze(0).permute(1, 2, 0).clamp(0, 1).cpu().numpy()
                   * 255).astype(np.uint8)
            res = Image.fromarray(out, 'RGB').convert('RGBA')
            alpha = rgba.getchannel('A').resize(
                (rgba.width * scale, rgba.height * scale), Image.LANCZOS)
            res.putalpha(alpha)
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
