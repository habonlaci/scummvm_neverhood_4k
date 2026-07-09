#!/usr/bin/env python3
"""rebake_palettes.py -- re-generate loose 4x PNGs for bitmaps that have no
embedded palette, using the resource->palette map built by palette_map.py.

The engine (this repo) shows replacement PNGs verbatim, so sprites whose
palette is only assigned at runtime (scene palette) were previously baked
as grayscale noise. This tool overwrites those PNGs in
<loose>/images with correctly colored ones:

  * palette ref "PPPPPPPP": a palette resource, or a bitmap resource with
    an embedded palette (PaletteResource::load accepts both);
  * palette ref "smk:HHHHHHHH": the first-frame palette of that Smacker
    video (navigation scenes show cursors over video, and the video's
    palette is the active one).

Naming/format matches ResourceMan::loadUpscaledResource and the existing
build_loose_data.py pipeline: %08X.png, RGBA, index 0 transparent for
sprites, Lanczos x<factor>.

Re-runnable; only touches hashes present in the map (or those given with
--hash).

Usage:
  python rebake_palettes.py [--map <path-to>/palette_map.json]
                            [--data-dir DIR] [--images DIR] [--factor 4]
                            [--hash HHHHHHHH ...] [--dry-run]
"""

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhk4k_lib as nhk
import nhk4k_paths as P

DEFAULT_DATA_DIR = P.DATA_DIR
DEFAULT_MAP = P.PALETTE_MAP
DEFAULT_IMAGES = os.path.join(DEFAULT_DATA_DIR, "loose_4k", "images")

REPO_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))


# ---------------------------------------------------------------------------
# Smacker first-frame palette (video/smk_decoder.cpp semantics)
# ---------------------------------------------------------------------------

_SMK_PALMAP = [(i << 2) | (i >> 4) for i in range(64)]


def smk_first_palette(data):
    """Return the 256-entry [(r,g,b)] palette of a Smacker file's frame 0."""
    if data[:3] != b"SMK":
        raise ValueError("not a Smacker file")
    frames = struct.unpack_from("<I", data, 12)[0]
    # header: sig(4) w(4) h(4) frames(4) rate(4) flags(4) audioSize(7*4)
    #         treesSize(4) mmap(4) mclr(4) full(4) type(4) audioRate(7*4)
    #         dummy(4) = 104 bytes
    trees_size = struct.unpack_from("<I", data, 52)[0]
    pos = 104 + 4 * frames  # skip frame sizes
    type0 = data[pos]
    pos += frames + trees_size
    if not (type0 & 1):
        raise ValueError("frame 0 has no palette record")
    chunk_len = data[pos] * 4
    chunk = data[pos + 1:pos + chunk_len]
    prev = [(0, 0, 0)] * 256
    pal = []
    i = 0
    j = 0  # read index into prev
    while len(pal) < 256 and i < len(chunk):
        b = chunk[i]
        if b & 0x80:
            n = (b & 0x7F) + 1
            pal += prev[j:j + n]
            j += n
            i += 1
        elif b & 0x40:
            n = (b & 0x3F) + 1
            src = chunk[i + 1]
            pal += prev[src:src + n]
            j = src + n
            i += 2
        else:
            pal.append((_SMK_PALMAP[b & 0x3F],
                        _SMK_PALMAP[chunk[i + 1] & 0x3F],
                        _SMK_PALMAP[chunk[i + 2] & 0x3F]))
            i += 3
    while len(pal) < 256:
        pal.append((0, 0, 0))
    return pal[:256]


# ---------------------------------------------------------------------------


def resolve_palette(rs, ref, cache):
    """ref: 'PPPPPPPP' (palette res or bitmap with palette) or 'smk:HHHHHHHH'.
    Returns [(r,g,b)]*256 or raises."""
    if ref in cache:
        return cache[ref]
    if ref.lower().startswith("smk:"):
        h = int(ref[4:], 16)
        data = rs.load(h)
        if data is None:
            raise ValueError("smacker %s not found" % ref)
        pal = smk_first_palette(data)
    else:
        h = int(ref, 16)
        entry = rs.find(h)
        if entry is None:
            raise ValueError("palette resource %s not found" % ref)
        data = entry.archive.load(entry)
        if entry.type_name == "palette":
            pal = nhk.rgbx_to_rgb(data[:1024])
        elif entry.type_name == "bitmap":
            bmp = nhk.parse_bitmap_resource(data)
            if bmp.palette is None:
                raise ValueError("bitmap %s has no embedded palette" % ref)
            pal = nhk.rgbx_to_rgb(bmp.palette)
        else:
            raise ValueError("%s is a %s, not palette/bitmap" %
                             (ref, entry.type_name))
    cache[ref] = pal
    return pal


def bake_bitmap(Image, rs, file_hash, rgb, factor, out_dir):
    entry = rs.find(file_hash)
    if entry is None or entry.type_name != "bitmap":
        raise ValueError("not a bitmap resource")
    bmp = nhk.parse_bitmap_resource(entry.archive.load(entry))
    if bmp.pixels is None:
        raise ValueError("palette-only bitmap, nothing to draw")
    if bmp.rle:
        indices = nhk.unpack_sprite_rle(bmp.pixels, bmp.width, bmp.height)
    else:
        indices = nhk.unpack_sprite_normal(bmp.pixels, bmp.width, bmp.height)

    # same transparency rule as extract_assets.py / build_loose_data.py
    is_background = (not bmp.rle and bmp.width >= 640 and bmp.height >= 480)

    img = Image.frombytes("P", (bmp.width, bmp.height), bytes(indices))
    flat = []
    for r, g, b in rgb:
        flat.extend((r, g, b))
    img.putpalette(flat)
    img = img.convert("RGBA")
    if not is_background:
        alpha = bytes(0 if i == 0 else 255 for i in indices)
        img.putalpha(Image.frombytes("L", (bmp.width, bmp.height), alpha))
    img = img.resize((bmp.width * factor, bmp.height * factor), Image.LANCZOS)
    path = os.path.join(out_dir, nhk.hash_name(file_hash) + ".png")
    img.save(path)
    return path, bmp.width, bmp.height


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=DEFAULT_MAP)
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--images", default=DEFAULT_IMAGES,
                    help="loose images dir to overwrite (outside the repo)")
    ap.add_argument("--factor", type=int, default=4)
    ap.add_argument("--hash", action="append", default=[],
                    help="only rebake these hashes")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.images)
    if os.path.normcase(out_dir).startswith(REPO_ROOT):
        ap.error("--images must be OUTSIDE the repository")
    os.makedirs(out_dir, exist_ok=True)

    try:
        from PIL import Image
    except ImportError:
        sys.exit("Pillow is required")

    pmap = json.load(open(args.map))["hashes"]
    if args.hash:
        want = set()
        for h in args.hash:
            for part in h.split(","):
                if part.strip():
                    want.add(part.strip().upper())
        pmap = {k: v for k, v in pmap.items() if k in want}

    import glob as _glob
    rs = nhk.ResourceSet()
    for p in sorted({os.path.normcase(q): q for q in
                     _glob.glob(os.path.join(args.data_dir, "*.BLB")) +
                     _glob.glob(os.path.join(args.data_dir, "*.blb"))}.values()):
        rs.add_archive(p)

    cache = {}
    ok = err = 0
    for key in sorted(pmap):
        ref = pmap[key]
        try:
            rgb = resolve_palette(rs, ref, cache)
            if args.dry_run:
                print("DRY  %s <- %s" % (key, ref))
                ok += 1
                continue
            path, w, h = bake_bitmap(Image, rs, int(key, 16), rgb,
                                     args.factor, out_dir)
            print("OK   %s %dx%d <- %s" % (path, w, h, ref))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print("ERR  %s <- %s: %s" % (key, ref, exc))
            err += 1

    print("\nrebaked %d, errors %d -> %s" % (ok, err, out_dir))
    rs.close()


if __name__ == "__main__":
    main()
