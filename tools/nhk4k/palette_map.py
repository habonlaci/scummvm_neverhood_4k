#!/usr/bin/env python3
"""palette_map.py -- derive a resource-hash -> palette-hash map from the
Neverhood engine sources.

Many bitmap/animation resources carry no embedded palette; at runtime the
engine applies the current scene's palette (Scene::setPalette, usually the
scene background bitmap, which DOES embed a palette -- see
engines/neverhood/scene.cpp and palette.cpp: PaletteResource::load accepts
either a palette resource or a bitmap with an embedded palette).

This tool statically recovers that association by parsing the C++ scene
code:

  * every `Class::method` body in engines/neverhood/*.cpp and modules/*.cpp
    is collected per class;
  * a class gets a palette from `setPalette(0x...)`, else
    `setBackground(0x...)` / `changeBackground(0x...)` (Scene::setPalette
    defaults to the background hash), else by resolving a literal passed
    through a base-class constructor whose body calls
    setPalette(param)/setBackground(param) (e.g. GameStateMenu);
  * palettes propagate from scenes to the sprite classes they create
    (`insertSprite<T>`, `new T(`) to a fixpoint, so hashes inside
    module*_sprites.cpp constructors/handlers get the owning scene palette;
  * `createStaticScene(bg, cursor)` maps cursor -> bg directly;
  * file-scope `... kFoo[] = { 0x..., }` arrays are attributed to every
    class whose methods reference them.

Every 7-8 digit hex literal in a palette-owning class that names a real
bitmap/animation resource (per manifest.json) is then mapped to that
class's palette hash. Only resources WITHOUT an embedded palette are
emitted, since the others don't need it.

Output (outside the repo): palette_map.json
    { "hashes": { "HHHHHHHH": "PPPPPPPP", ... },
      "sources": { "HHHHHHHH": "menumodule.cpp:MainMenu", ... } }
plus a mapped/unmapped report on stdout.

Usage:
  python palette_map.py [--engine-dir DIR] [--manifest FILE] [--out FILE] [-v]
"""

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nhk4k_paths as P

REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_ENGINE = os.path.join(REPO_ROOT, "engines", "neverhood")
DEFAULT_MANIFEST = P.MANIFEST
DEFAULT_OUT = P.PALETTE_MAP
DEFAULT_DAT = P.NEVERHOOD_DAT

HEX_RE = re.compile(r"0[xX]([0-9A-Fa-f]{7,8})\b")
METHOD_RE = re.compile(r"^[\w:<>~ \t\*&]*?\b(\w+)::(~?\w+)\s*\(", re.M)
ARRAY_RE = re.compile(r"\b(k\w+|\w+FileHashes|\w+FileHashList)\s*\[\s*\]\s*=\s*\{([^}]*)\}")


def find_matching(text, pos, open_ch, close_ch):
    depth = 0
    for i in range(pos, len(text)):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return src


class Method:
    def __init__(self, cls, name, params, init, body, filename):
        self.cls = cls
        self.name = name
        self.params = params      # text between the signature parens
        self.init = init          # initializer-list text (ctors), may be ""
        self.body = body          # text between the outer braces
        self.filename = filename

    @property
    def text(self):
        return self.init + "\n" + self.body

    @property
    def is_ctor(self):
        return self.name == self.cls


def parse_file(path):
    """Yield Method objects for every Class::method definition in a file."""
    src = strip_comments(open(path, encoding="latin-1").read())
    fname = os.path.basename(path)
    out = []
    for m in METHOD_RE.finditer(src):
        cls, name = m.group(1), m.group(2)
        paren_open = src.index("(", m.end() - 1)
        paren_close = find_matching(src, paren_open, "(", ")")
        if paren_close < 0:
            continue
        # skip declarations / pure statements: need a '{' before the next ';'
        rest = src[paren_close + 1:]
        brace_rel = rest.find("{")
        semi_rel = rest.find(";")
        if brace_rel < 0 or (0 <= semi_rel < brace_rel):
            continue
        # initializer lists start with ':' and may themselves contain
        # parenthesized calls -- everything between ')' and '{' is init text
        init = rest[:brace_rel]
        if not init.lstrip().startswith(":"):
            init = ""
        body_open = paren_close + 1 + brace_rel
        body_close = find_matching(src, body_open, "{", "}")
        if body_close < 0:
            continue
        out.append(Method(cls, name, src[paren_open + 1:paren_close],
                          init, src[body_open + 1:body_close], fname))
    return out, src


def split_args(text):
    """Split a C++ argument list on top-level commas."""
    args, depth, cur = [], 0, []
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if cur and "".join(cur).strip():
        args.append("".join(cur).strip())
    return args


def param_names(params_text):
    names = []
    for p in split_args(params_text):
        p = p.split("=")[0].strip()
        ids = re.findall(r"\w+", p)
        names.append(ids[-1] if ids else "")
    return names


def hexes(text):
    return [int(h, 16) for h in HEX_RE.findall(text)]


def parse_neverhood_dat(path):
    """Parse neverhood.dat (engines/neverhood/staticdata.cpp,
    StaticData::load) and return two association lists:

      nav_cursor_pairs:  (mouseCursorFileHash, navigation smacker fileHash)
                         -- during a navigation scene the screen palette is
                         the video's, so cursors get the SMK palette;
      track_pairs:       (hash, bgFilename) for track scenes
                         (module2700 Scene2704: setPalette(tracks->bgFilename),
                         insertScreenMouse(tracks->mouseCursorFilename)).
    """
    with open(path, "rb") as f:
        data = f.read()
    pos = 8  # magic + version

    def u32():
        nonlocal pos
        v = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return v

    def u16():
        nonlocal pos
        v = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        return v

    def u8():
        nonlocal pos
        v = data[pos]
        pos += 1
        return v

    # message lists
    for _ in range(u32()):
        u32()
        for _ in range(u32()):
            pos += 6
    # rect lists
    for _ in range(u32()):
        u32()
        for _ in range(u32()):
            pos += 8
            for _ in range(u32()):
                pos += 12
    # hit rect lists
    for _ in range(u32()):
        u32()
        for _ in range(u32()):
            pos += 10
    # navigation lists
    nav_cursor_pairs = []
    for _ in range(u32()):
        u32()
        for _ in range(u32()):
            file_hash = u32()
            u32(); u32(); u32()   # left/right/middle smackers
            u8(); u8()            # interactive, middleFlag
            cursor = u32()
            if cursor and file_hash:
                nav_cursor_pairs.append((cursor, file_hash))
    # hall of records (backgrounds carry their own palettes; nothing to map)
    for _ in range(u32()):
        pos += 4 + 16 + 2
    # track infos
    track_pairs = []
    for _ in range(u32()):
        u32()
        bg = u32()
        bg_shadow = u32()
        u32(); u32(); u32()       # dataResource, trackPoints, rectList
        u32(); u32()              # exPaletteFilename2/1
        cursor = u32()
        u16(); u16()
        for h in (bg_shadow, cursor):
            if h:
                track_pairs.append((h, bg))
    return nav_cursor_pairs, track_pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-dir", default=DEFAULT_ENGINE)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--dat", default=DEFAULT_DAT,
                    help="neverhood.dat for navigation/track associations "
                         "('' to skip)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if os.path.normcase(os.path.abspath(args.out)).startswith(
            os.path.normcase(REPO_ROOT)):
        ap.error("--out must be OUTSIDE the repository")

    manifest = json.load(open(args.manifest))
    res_by_hash = {int(r["hash"], 16): r for r in manifest["resources"]}
    # Animation containers (anim_container) are never requested by hash --
    # the engine loads the playable link hashes, and every playable
    # animation in the archives embeds its own palette (paletteDataOfs>0),
    # so only bitmaps can actually lack a palette.
    needs_palette = {h for h, r in res_by_hash.items()
                     if r["type"] in ("bitmap", "animation")
                     and not r.get("has_palette")
                     and not r.get("anim_container")}
    pal_sources = {h for h, r in res_by_hash.items()
                   if r["type"] == "palette"
                   or (r["type"] == "bitmap" and r.get("has_palette"))}

    cpp_files = []
    for sub in ("", "modules"):
        d = os.path.join(args.engine_dir, sub)
        cpp_files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.endswith(".cpp")]

    classes = defaultdict(list)     # class -> [Method]
    file_arrays = {}                # array name -> [hashes]
    static_scene_pairs = []         # (cursor_hash, bg_hash)
    for path in cpp_files:
        methods, src = parse_file(path)
        for meth in methods:
            classes[meth.cls].append(meth)
        for am in ARRAY_RE.finditer(src):
            vals = hexes(am.group(2))
            if vals:
                file_arrays.setdefault(am.group(1), vals)
        for sm in re.finditer(
                r"createStaticScene\s*\(\s*0[xX]([0-9A-Fa-f]+)\s*,"
                r"\s*0[xX]([0-9A-Fa-f]+)", src):
            static_scene_pairs.append((int(sm.group(2), 16),
                                       int(sm.group(1), 16)))

    # ---- pass 1: direct palette per class ------------------------------
    class_palette = {}     # class -> (palette_hash, why)
    for cls, methods in classes.items():
        cand = None
        for pat, why in ((r"setPalette\s*\(\s*0[xX]([0-9A-Fa-f]+)", "setPalette"),
                         (r"(?:setBackground|changeBackground)\s*\(\s*0[xX]([0-9A-Fa-f]+)", "setBackground")):
            for meth in sorted(methods, key=lambda m: not m.is_ctor):
                mm = re.search(pat, meth.text)
                if mm:
                    cand = (int(mm.group(1), 16), why)
                    break
            if cand:
                break
        if not cand:
            # setPalette(kSomeArray[index]) -- runtime-selected background
            # (e.g. Scene2901 teleporter locations). Approximate with the
            # array's first nonzero entry: far better than grayscale, and
            # these location variants share very similar palettes.
            for meth in methods:
                mm = re.search(r"(?:setPalette|setBackground)\s*\(\s*(\w+)\s*\[",
                               meth.text)
                if mm and mm.group(1) in file_arrays:
                    vals = [v for v in file_arrays[mm.group(1)] if v]
                    if vals:
                        cand = (vals[0], "array %s[0]" % mm.group(1))
                        break
        if cand:
            class_palette[cls] = cand

    # ---- pass 2: literal routed through a base-class ctor param --------
    # e.g. SaveGameMenu : GameStateMenu(..., 0x30084E25 /*background*/, ...)
    base_pal_param = {}    # base class -> param index that reaches setPalette
    for cls, methods in classes.items():
        for meth in methods:
            if not meth.is_ctor:
                continue
            names = param_names(meth.params)
            for pat in (r"setPalette\s*\(\s*(\w+)\s*\)",
                        r"setBackground\s*\(\s*(\w+)\s*\)"):
                mm = re.search(pat, meth.body)
                if mm and mm.group(1) in names:
                    base_pal_param[cls] = names.index(mm.group(1))
                    break
            if cls in base_pal_param:
                break
    for cls, methods in classes.items():
        if cls in class_palette:
            continue
        for meth in methods:
            if not meth.is_ctor or not meth.init:
                continue
            for base, idx in base_pal_param.items():
                bm = re.search(r"\b%s\s*\(" % re.escape(base), meth.init)
                if not bm:
                    continue
                close = find_matching(meth.init, bm.end() - 1, "(", ")")
                call_args = split_args(meth.init[bm.end():close])
                if idx < len(call_args):
                    hh = hexes(call_args[idx])
                    if hh:
                        class_palette[cls] = (hh[0], "base:%s" % base)

    # ---- pass 3: propagate scene palettes to created sprite classes ----
    creates = defaultdict(set)   # class -> classes it instantiates
    for cls, methods in classes.items():
        text = "\n".join(m.text for m in methods)
        for cm in re.finditer(r"insert(?:Sprite|Klaymen)\s*<\s*(\w+)", text):
            creates[cls].add(cm.group(1))
        for cm in re.finditer(r"\bnew\s+(\w+)\s*\(", text):
            if cm.group(1) in classes:
                creates[cls].add(cm.group(1))
    changed = True
    while changed:
        changed = False
        for cls, (pal, why) in list(class_palette.items()):
            for child in creates.get(cls, ()):
                if child not in class_palette:
                    class_palette[child] = (pal, "via %s" % cls)
                    changed = True

    # ---- collect hash -> palette votes ---------------------------------
    votes = defaultdict(Counter)     # hash -> Counter(palette)
    origin = defaultdict(dict)       # hash -> palette -> "file:Class"
    for cls, (pal, why) in class_palette.items():
        text = "\n".join(m.text for m in classes[cls])
        found = set(hexes(text))
        for arr, vals in file_arrays.items():
            if re.search(r"\b%s\b" % re.escape(arr), text):
                found.update(vals)
        src_name = "%s:%s" % (classes[cls][0].filename, cls)
        for h in found:
            if h in needs_palette and h != pal:
                votes[h][pal] += 1
                origin[h].setdefault(pal, src_name)
    for cursor, bg in static_scene_pairs:
        if cursor in needs_palette:
            votes[cursor][bg] += 1
            origin[cursor].setdefault(bg, "createStaticScene")

    # ---- neverhood.dat: navigation cursors and track scenes -------------
    smk_map = {}   # hash -> video hash whose SMK palette to use
    if args.dat and os.path.isfile(args.dat):
        nav_pairs, track_pairs = parse_neverhood_dat(args.dat)
        videos = {h for h, r in res_by_hash.items() if r["type"] == "video"}
        for h, bg in track_pairs:
            if h in needs_palette and bg in pal_sources:
                votes[h][bg] += 1
                origin[h].setdefault(bg, "neverhood.dat:TrackInfo")
        for cursor, smk in nav_pairs:
            if cursor in needs_palette and cursor not in votes and smk in videos:
                smk_map.setdefault(cursor, smk)
    elif args.dat:
        print("WARNING: %s not found, skipping navigation/track data"
              % args.dat)

    hash_map, sources, conflicts, bad_pal = {}, {}, 0, 0
    for h, ctr in sorted(votes.items()):
        ranked = [p for p, _n in ctr.most_common() if p in pal_sources]
        if not ranked:
            bad_pal += 1
            continue
        if len(set(ctr)) > 1:
            conflicts += 1
        pal = ranked[0]
        hash_map["%08X" % h] = "%08X" % pal
        sources["%08X" % h] = origin[h][pal]
        if args.verbose:
            print("%08X -> %08X  (%s)%s" % (h, pal, origin[h][pal],
                  "  CONFLICT %s" % dict(ctr) if len(ctr) > 1 else ""))
    for h, smk in sorted(smk_map.items()):
        key = "%08X" % h
        if key not in hash_map:
            hash_map[key] = "smk:%08X" % smk
            sources[key] = "neverhood.dat:NavigationList"

    with open(args.out, "w") as f:
        json.dump({"hashes": hash_map, "sources": sources}, f, indent=1,
                  sort_keys=True)

    mapped = set(int(k, 16) for k in hash_map)
    unmapped = sorted(needs_palette - mapped)
    n_bmp = sum(1 for h in needs_palette if res_by_hash[h]["type"] == "bitmap")
    n_bmp_m = sum(1 for h in mapped if res_by_hash[h]["type"] == "bitmap")
    n_anim = len(needs_palette) - n_bmp
    n_anim_m = len(mapped) - n_bmp_m
    print("\nclasses with a palette: %d (of %d classes)"
          % (len(class_palette), len(classes)))
    print("no-embedded-palette bitmaps    : %4d mapped / %4d total" % (n_bmp_m, n_bmp))
    print("no-embedded-palette animations : %4d mapped / %4d total" % (n_anim_m, n_anim))
    print("conflicting associations (majority vote used): %d" % conflicts)
    print("dropped (mapped palette not a palette source): %d" % bad_pal)
    print("unmapped: %d -> not referenced by literal hash in any "
          "palette-owning class (navigation scenes use data resources, "
          "smacker scenes take palettes from the video)" % len(unmapped))
    if args.verbose:
        for h in unmapped:
            print("  UNMAPPED %08X (%s)" % (h, res_by_hash[h]["type"]))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
