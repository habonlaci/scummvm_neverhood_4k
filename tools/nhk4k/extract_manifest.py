#!/usr/bin/env python3
"""extract_manifest.py -- walk all *.BLB archives and emit manifest.json.

Part of The Neverhood 4K remaster tooling. Formats follow the engine
sources in engines/neverhood/ (see nhk4k_lib.py for references).

One manifest entry per resource (per file hash, engine view: the entry with
the newest timestamp wins across archives, comprType 0x65 links resolved):

  hash        8-hex uppercase hash, identical to the loose-PNG replacement
              base name used by ResourceMan::loadUpscaledResource
  type        resource type name (bitmap/palette/animation/data/text/
              sound/music/video)
  archive     source .BLB file name
  offset      byte offset of the data inside the archive
  disk_size   compressed (on-disk) size
  size        uncompressed size
  compression "none" | "dcl" | "link"
  width/height   for bitmaps (from the bitmap header; extData cross-checked)
  rle            for bitmaps
  has_palette    for bitmaps (embedded 1024-byte palette present)
  position       for bitmaps that carry a position
  frame_count    for animations
  link_target    for link entries (hash of the entry it redirects to)

Usage:
  python extract_manifest.py --data-dir <path-to>/image/DATA
                             [--out <path-to>/manifest.json]

The manifest is written OUTSIDE the repo by default; never point --out
into the source tree.
"""

import argparse
import glob
import json
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nhk4k_lib as nhk
import nhk4k_paths as P

DEFAULT_DATA_DIR = P.DATA_DIR
DEFAULT_OUT = P.MANIFEST


def bitmap_info(rs: nhk.ResourceSet, entry: nhk.BlbEntry) -> dict:
    """Dimensions/flags for a bitmap resource without full decompression:
    the header (flags + dims + pos) fits in the first 10 bytes."""
    info = {}
    try:
        prefix = entry.archive.load(entry, max_out=10)
    except Exception as exc:  # noqa: BLE001 - record and continue
        info["error"] = "header: %s" % exc
        return info
    if len(prefix) < 2:
        info["error"] = "short bitmap header"
        return info
    flags = struct.unpack_from("<H", prefix, 0)[0]
    info["rle"] = bool(flags & nhk.BF_RLE)
    info["has_palette"] = bool(flags & nhk.BF_HAS_PALETTE)
    pos = 2
    if flags & nhk.BF_HAS_DIMENSIONS:
        info["width"], info["height"] = struct.unpack_from("<HH", prefix, pos)
        pos += 4
    else:
        info["width"], info["height"] = 1, 1
    if flags & nhk.BF_HAS_POSITION:
        x, y = struct.unpack_from("<HH", prefix, pos)
        info["position"] = [x, y]
    # extData (when present) also stores width/height (AnimResource::
    # loadSpriteDimensions reads them from there); cross-check.
    ext = rs.ext_data(entry.file_hash)
    if ext is not None and len(ext) >= 4:
        ew, eh = struct.unpack_from("<HH", ext, 0)
        if (ew, eh) not in ((0, 0), (info["width"], info["height"])):
            info["ext_dimensions"] = [ew, eh]
    return info


def animation_info(entry: nhk.BlbEntry, file_hash: int,
                   anim_cache: dict) -> dict:
    """Frame count for one hash of an animation resource. Only the header
    and anim list (12 + 8*count bytes) are decompressed, and the result is
    cached per physical entry since many hashes share one anim blob."""
    key = (entry.archive.name, entry.index)
    info_obj = anim_cache.get(key)
    if info_obj is None:
        try:
            prefix = entry.archive.load(entry, max_out=12)
            need = nhk.anim_header_size(prefix)
            prefix = entry.archive.load(entry, max_out=need)
            info_obj = nhk.parse_anim_header(prefix)
        except Exception as exc:  # noqa: BLE001
            info_obj = exc
        anim_cache[key] = info_obj
    if isinstance(info_obj, Exception):
        return {"error": "anim header: %s" % info_obj}
    for anim_hash, frame_count, _ofs in info_obj.anim_list:
        if anim_hash == file_hash:
            return {"frame_count": frame_count,
                    "has_palette": info_obj.palette_data_ofs > 0}
    # The entry's own hash is not in its anim list: this is a shared data
    # container that playable anim hashes reference via 0x65 link entries.
    # Its frames are counted under those link hashes instead.
    return {"anim_container": True,
            "contains": [nhk.hash_name(h) for h, _fc, _o in info_obj.anim_list]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="directory containing the *.BLB archives")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="output manifest.json path (keep it outside the repo)")
    args = ap.parse_args()

    blb_paths = sorted(glob.glob(os.path.join(args.data_dir, "*.BLB")) +
                       glob.glob(os.path.join(args.data_dir, "*.blb")))
    # de-dup case-insensitive filesystems
    blb_paths = sorted({os.path.normcase(p): p for p in blb_paths}.values())
    if not blb_paths:
        ap.error("no .BLB files found in %s" % args.data_dir)

    rs = nhk.ResourceSet()
    for path in blb_paths:
        archive = rs.add_archive(path)
        print("read %-8s %7d entries" % (archive.name, len(archive.entries)))

    anim_cache = {}
    resources = []
    type_counts = Counter()
    total_anim_frames = 0
    errors = 0

    for file_hash in sorted(rs.entries):
        first = rs.entries[file_hash]
        entry = rs.find(file_hash)  # follows 0x65 links

        rec = {
            "hash": nhk.hash_name(file_hash),
            "type": (entry or first).type_name,
        }
        if first.is_link:
            rec["compression"] = "link"
            rec["link_target"] = nhk.hash_name(first.link_target)
            rec["archive"] = first.archive.name
        if entry is None:
            rec["error"] = "broken link"
            errors += 1
            resources.append(rec)
            type_counts["broken-link"] += 1
            continue
        if not first.is_link:
            rec["compression"] = {nhk.COMPR_UNCOMPRESSED: "none",
                                  nhk.COMPR_DCL: "dcl"}.get(
                entry.compr_type, "unknown(%d)" % entry.compr_type)
        rec["archive"] = entry.archive.name
        rec["offset"] = entry.offset
        rec["disk_size"] = entry.disk_size
        rec["size"] = entry.size

        if entry.type_name == "bitmap":
            rec.update(bitmap_info(rs, entry))
        elif entry.type_name == "animation":
            rec.update(animation_info(entry, file_hash, anim_cache))
            total_anim_frames += rec.get("frame_count", 0)
        if "error" in rec:
            errors += 1

        type_counts[entry.type_name] += 1
        resources.append(rec)

    manifest = {
        "data_dir": os.path.abspath(args.data_dir),
        "archives": [a.name for a in rs.archives],
        "resource_count": len(resources),
        "shadowed_duplicates": rs.shadowed,
        "resources": resources,
    }

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("\nwrote %s (%d resources)" % (out_path, len(resources)))

    # ------------------------------------------------------------------ stats
    print("\nSummary")
    print("-------")
    for name in sorted(type_counts):
        print("  %-12s %6d" % (name, type_counts[name]))
    print("  %-12s %6d" % ("TOTAL", sum(type_counts.values())))
    print()
    print("  bitmaps (incl. backgrounds/sprites): %6d" % type_counts["bitmap"])
    print("  total animation frames:              %6d" % total_anim_frames)
    print("  Smacker videos:                      %6d" % type_counts["video"])
    print("  shadowed duplicate entries:          %6d" % rs.shadowed)
    if errors:
        print("  entries with errors:                 %6d" % errors)

    rs.close()


if __name__ == "__main__":
    main()
