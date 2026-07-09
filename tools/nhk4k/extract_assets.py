#!/usr/bin/env python3
"""extract_assets.py -- extract resources from The Neverhood BLB archives.

Part of The Neverhood 4K remaster tooling. Formats follow the engine
sources in engines/neverhood/ (see nhk4k_lib.py for references).

Selection:
  --hash HHHHHHHH     one or more 8-hex resource hashes (repeatable and/or
                      comma separated)
  --type NAME         one or more of: bitmap, palette, animation, data,
                      text, sound, music, video (repeatable)
  --limit N           stop after N extracted resources (safety valve)

Output (never inside the repo, default <NHK4K_ROOT>/extracted):
  bitmaps     <out>/bitmap/HHHHHHHH.png          (RGBA; palette applied;
              index 0 transparent for sprites -- see --opaque)
  animations  <out>/animation/HHHHHHHH-000.png ... (one PNG per frame)
              The names match ResourceMan::loadUpscaledResource() exactly:
              "%08X.png" and "%08X-%03d.png", uppercase, zero padded.
  video       <out>/video/HHHHHHHH.smk           (raw Smacker file)
  palette     <out>/palette/HHHHHHHH.pal         (raw 1024-byte RGBX)
              plus HHHHHHHH.png 16x16 swatch preview
  text        <out>/text/HHHHHHHH.txt
  data        <out>/data/HHHHHHHH.bin
  sound/music skipped unless --dump-raw, then <out>/sound/HHHHHHHH.raw
              (16-bit DW-ADPCM-ish shifted PCM, 22050 Hz -- see
              NeverhoodAudioStream; the shift value from extData is put in
              the sidecar name HHHHHHHH.shift<N>.raw)

Palette association (engine logic, PaletteResource::load):
  * a bitmap with an embedded palette uses it;
  * animation resources carry their palette at paletteDataOfs;
  * otherwise the palette comes from a DIFFERENT resource chosen by the
    game scripts at scene-build time -- that association is not stored in
    the archives, so pass --palette HHHHHHHH (a palette resource, or a
    bitmap containing one, typically the scene background) to apply one;
  * with no palette available a grayscale ramp is used and a warning printed.

Usage examples:
  python extract_assets.py --hash 0E018400
  python extract_assets.py --type video
  python extract_assets.py --type bitmap --limit 50
  python extract_assets.py --hash 08C6B0C1 --palette 04086520
"""

import argparse
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhk4k_lib as nhk
import nhk4k_paths as P

DEFAULT_DATA_DIR = P.DATA_DIR
DEFAULT_OUT = P.EXTRACTED

REPO_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))


def _require_pillow():
    try:
        from PIL import Image  # noqa: F401
        return Image
    except ImportError:
        sys.exit("Pillow is required for PNG output. Install it with:\n"
                 '  "%s" -m pip install --user pillow' % sys.executable)


def indices_to_rgba(Image, indices, width, height, rgb_palette,
                    index0_transparent, shadow64=False):
    img = Image.frombytes("P", (width, height), bytes(indices))
    flat = []
    for r, g, b in rgb_palette:
        flat.extend((r, g, b))
    img.putpalette(flat)
    img = img.convert("RGBA")
    if index0_transparent:
        # alpha 0 wherever the source index was 0 (the engine's transparent
        # color for sprites / untouched RLE pixels).
        # Index 64 is the engine's reserved shadow color in animations
        # (scenes call setRepl(64, 0) at runtime, which RGBA replacements
        # bypass): bake it as translucent black so shadows look right.
        alpha = bytes(0 if i == 0 else (110 if shadow64 and i == 64 else 255)
                      for i in indices)
        img.putalpha(Image.frombytes("L", (width, height), alpha))
        if shadow64 and 64 in indices:
            px = img.load()
            k = 0
            for y in range(height):
                for x in range(width):
                    if indices[k] == 64:
                        px[x, y] = (0, 0, 0, 110)
                    k += 1
    return img


def load_external_palette(rs, palette_hash_str):
    """PaletteResource::load: accepts a palette resource or a bitmap that
    embeds one."""
    file_hash = int(palette_hash_str, 16)
    entry = rs.find(file_hash)
    if entry is None:
        sys.exit("--palette %s: no such resource" % palette_hash_str)
    data = entry.archive.load(entry)
    if entry.type_name == "palette":
        pal = data[:1024]
    elif entry.type_name == "bitmap":
        bmp = nhk.parse_bitmap_resource(data)
        if bmp.palette is None:
            sys.exit("--palette %s: bitmap has no embedded palette"
                     % palette_hash_str)
        pal = bmp.palette
    else:
        sys.exit("--palette %s: resource is a %s, not palette/bitmap"
                 % (palette_hash_str, entry.type_name))
    return nhk.rgbx_to_rgb(pal)


def extract_bitmap(Image, rs, entry, out_dir, ext_palette, force_opaque):
    data = entry.archive.load(entry)
    bmp = nhk.parse_bitmap_resource(data)
    name = nhk.hash_name(entry.file_hash)
    if bmp.pixels is None:
        # palette-only bitmap (used as palette carrier)
        if bmp.palette is not None:
            path = os.path.join(out_dir, name + ".pal")
            with open(path, "wb") as f:
                f.write(bmp.palette)
            return path + " (palette-only bitmap)"
        return None

    if bmp.rle:
        indices = nhk.unpack_sprite_rle(bmp.pixels, bmp.width, bmp.height)
    else:
        indices = nhk.unpack_sprite_normal(bmp.pixels, bmp.width, bmp.height)

    if bmp.palette is not None:
        rgb = nhk.rgbx_to_rgb(bmp.palette)
        pal_src = "embedded"
    elif ext_palette is not None:
        rgb = ext_palette
        pal_src = "--palette"
    else:
        rgb = nhk.GRAYSCALE_PALETTE
        pal_src = "grayscale (no embedded palette; pass --palette)"

    # Sprites get index 0 as transparent. Full-screen non-RLE bitmaps with
    # their own palette are backgrounds and stay opaque.
    is_background = (not bmp.rle and bmp.width >= 640 and bmp.height >= 480)
    transparent = not (is_background or force_opaque)

    img = indices_to_rgba(Image, indices, bmp.width, bmp.height, rgb,
                          transparent)
    path = os.path.join(out_dir, name + ".png")
    img.save(path)
    return "%s %dx%d rle=%d palette=%s%s" % (
        path, bmp.width, bmp.height, bmp.rle, pal_src,
        " pos=%d,%d" % bmp.position if bmp.position else "")


def extract_animation(Image, rs, entry, file_hash, out_dir, ext_palette):
    data = entry.archive.load(entry)
    info = nhk.parse_anim_header(data)
    hashes = [file_hash]
    if all(h != file_hash for h, _fc, _o in info.anim_list):
        # container blob: extract every animation it holds, each under its
        # own (playable, link-entry) hash name
        hashes = [h for h, _fc, _o in info.anim_list]

    if info.palette_data_ofs > 0:
        rgb = nhk.rgbx_to_rgb(data[info.palette_data_ofs:
                                   info.palette_data_ofs + 1024])
        pal_src = "embedded"
    elif ext_palette is not None:
        rgb = ext_palette
        pal_src = "--palette"
    else:
        rgb = nhk.GRAYSCALE_PALETTE
        pal_src = "grayscale (no embedded palette; pass --palette)"

    sprite_data = data[info.sprite_data_ofs:]
    written = total = 0
    for anim_hash in hashes:
        frames = nhk.parse_anim_frames(data, info, anim_hash)
        name = nhk.hash_name(anim_hash)
        total += len(frames)
        for i, fr in enumerate(frames):
            if fr.width <= 0 or fr.height <= 0:
                continue
            # AnimResource::draw always uses unpackSpriteRle for anim frames
            indices = nhk.unpack_sprite_rle(sprite_data[fr.sprite_data_offs:],
                                            fr.width, fr.height)
            img = indices_to_rgba(Image, indices, fr.width, fr.height, rgb,
                                  True, shadow64=True)
            img.save(os.path.join(out_dir, "%s-%03d.png" % (name, i)))
            written += 1
    return "%s: anims [%s], %d/%d frames, palette=%s" % (
        out_dir, " ".join(nhk.hash_name(h) for h in hashes), written, total,
        pal_src)


def extract_video(rs, entry, out_dir):
    data = entry.archive.load(entry)
    name = nhk.hash_name(entry.file_hash)
    path = os.path.join(out_dir, name + ".smk")
    with open(path, "wb") as f:
        f.write(data)
    magic = data[:4].decode("ascii", "replace")
    return "%s (%d bytes, magic=%s)" % (path, len(data), magic)


def extract_palette(Image, rs, entry, out_dir):
    data = entry.archive.load(entry)
    name = nhk.hash_name(entry.file_hash)
    path = os.path.join(out_dir, name + ".pal")
    with open(path, "wb") as f:
        f.write(data[:1024])
    rgb = nhk.rgbx_to_rgb(data[:1024])
    img = Image.new("RGB", (16, 16))
    img.putdata(rgb)
    img.resize((256, 256), Image.NEAREST).save(
        os.path.join(out_dir, name + ".png"))
    return path


def extract_text(rs, entry, out_dir):
    # TextResource: u32 count, count+? u32 offsets, then string pool
    # (see TextResource::getString: pool starts at 4 + count*4)
    data = entry.archive.load(entry)
    name = nhk.hash_name(entry.file_hash)
    count = struct.unpack_from("<I", data, 0)[0]
    pool = 4 + count * 4
    lines = []
    for i in range(count):
        start = pool + struct.unpack_from("<I", data, 4 + i * 4)[0]
        end = data.find(b"\0", start)
        if end < 0:
            end = len(data)
        lines.append(data[start:end].decode("latin-1", "replace"))
    path = os.path.join(out_dir, name + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return "%s (%d strings)" % (path, count)


def extract_raw(rs, entry, out_dir, ext):
    data = entry.archive.load(entry)
    name = nhk.hash_name(entry.file_hash)
    path = os.path.join(out_dir, name + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main():
    ap = argparse.ArgumentParser(
        description="Extract Neverhood BLB resources",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="output root (must be outside the repo)")
    ap.add_argument("--hash", action="append", default=[],
                    help="8-hex resource hash(es), comma separable")
    ap.add_argument("--type", action="append", default=[],
                    choices=sorted(set(nhk.RES_TYPE_NAMES.values())),
                    help="extract every resource of this type")
    ap.add_argument("--palette", default=None, metavar="HASH",
                    help="apply this palette resource (or a bitmap embedding "
                         "one) to images that have no embedded palette")
    ap.add_argument("--limit", type=int, default=None,
                    help="extract at most N resources")
    ap.add_argument("--opaque", action="store_true",
                    help="do not make palette index 0 transparent")
    ap.add_argument("--dump-raw", action="store_true",
                    help="raw-dump sound/music instead of skipping them")
    args = ap.parse_args()

    out_root = os.path.abspath(args.out)
    if os.path.normcase(out_root).startswith(REPO_ROOT):
        ap.error("--out must be OUTSIDE the repository (%s)" % REPO_ROOT)
    if not args.hash and not args.type:
        ap.error("select something: --hash and/or --type")

    Image = _require_pillow()

    blb_paths = sorted({os.path.normcase(p): p for p in
                        glob.glob(os.path.join(args.data_dir, "*.BLB")) +
                        glob.glob(os.path.join(args.data_dir, "*.blb"))}.values())
    if not blb_paths:
        ap.error("no .BLB files found in %s" % args.data_dir)

    rs = nhk.ResourceSet()
    for path in blb_paths:
        rs.add_archive(path)

    wanted_hashes = []
    for h in args.hash:
        for part in h.split(","):
            part = part.strip()
            if part:
                wanted_hashes.append(int(part, 16))
    wanted_types = set(args.type)

    ext_palette = (load_external_palette(rs, args.palette)
                   if args.palette else None)

    # Build the work list (hash selections first, then type sweeps)
    selected = []
    seen = set()
    for fh in wanted_hashes:
        if fh not in rs.entries:
            print("WARNING: hash %s not found in any archive"
                  % nhk.hash_name(fh))
            continue
        selected.append(fh)
        seen.add(fh)
    if wanted_types:
        for fh in sorted(rs.entries):
            if fh in seen:
                continue
            entry = rs.find(fh)
            if entry is not None and entry.type_name in wanted_types:
                selected.append(fh)

    if args.limit is not None:
        selected = selected[:args.limit]

    extracted = errors = skipped = 0
    for fh in selected:
        entry = rs.find(fh)
        if entry is None:
            print("SKIP %s: broken link" % nhk.hash_name(fh))
            skipped += 1
            continue
        tname = entry.type_name
        out_dir = os.path.join(out_root, tname)
        os.makedirs(out_dir, exist_ok=True)
        try:
            if tname == "bitmap":
                msg = extract_bitmap(Image, rs, entry, out_dir, ext_palette,
                                     args.opaque)
            elif tname == "animation":
                msg = extract_animation(Image, rs, entry, fh, out_dir,
                                        ext_palette)
            elif tname == "video":
                msg = extract_video(rs, entry, out_dir)
            elif tname == "palette":
                msg = extract_palette(Image, rs, entry, out_dir)
            elif tname == "text":
                msg = extract_text(rs, entry, out_dir)
            elif tname == "data":
                msg = extract_raw(rs, entry, out_dir, ".bin")
            elif tname in ("sound", "music"):
                if args.dump_raw:
                    ext_data = rs.ext_data(fh)
                    shift = ext_data[0] if ext_data else 255
                    msg = extract_raw(rs, entry, out_dir,
                                      ".shift%d.raw" % shift)
                else:
                    skipped += 1
                    continue
            else:
                msg = extract_raw(rs, entry, out_dir, ".bin")
        except Exception as exc:  # noqa: BLE001 - keep going, report at end
            print("ERROR %s (%s): %s" % (nhk.hash_name(fh), tname, exc))
            errors += 1
            continue
        if msg:
            print("OK   " + msg)
            extracted += 1
        else:
            skipped += 1

    print("\n%d extracted, %d skipped, %d errors -> %s"
          % (extracted, skipped, errors, out_root))
    rs.close()


if __name__ == "__main__":
    main()
