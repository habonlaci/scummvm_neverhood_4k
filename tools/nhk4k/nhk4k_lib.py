"""Shared library for The Neverhood 4K remaster asset tools.

Implements, in pure Python, the on-disk formats used by the Neverhood
ScummVM engine (this repo, engines/neverhood/):

  * BLB archives ..................... engines/neverhood/blbarchive.cpp
  * PKWare DCL "implode" (explode) ... common/dcl.cpp (algorithm; also the
                                        format of zlib contrib/blast.c)
  * Bitmap resources ................. engines/neverhood/graphics.cpp
                                        (parseBitmapResource, unpackSpriteRle,
                                        unpackSpriteNormal)
  * Animation resources .............. engines/neverhood/resource.cpp
                                        (AnimResource::load)
  * Loose-PNG naming convention ...... engines/neverhood/resourceman.cpp
                                        (loadUpscaledResource: "%08X.png" and
                                        "%08X-%03d.png", uppercase hex)

The DCL Huffman tables in dcl_tables.py were machine-derived from the trees
in ScummVM's common/dcl.cpp (GPL-3.0-or-later, same license as this project).

This file is part of the (GPL-3.0) Neverhood 4K project.
"""

import io
import os
import struct
from dataclasses import dataclass, field
from typing import Optional

from dcl_tables import LENGTH_TREE, DISTANCE_TREE, ASCII_TREE

# ---------------------------------------------------------------------------
# Resource types (engines/neverhood/resource.h)
# ---------------------------------------------------------------------------

RES_TYPE_NAMES = {
    2: "bitmap",
    3: "palette",
    4: "animation",
    5: "data",
    6: "text",
    7: "sound",
    8: "music",
    10: "video",
}

COMPR_UNCOMPRESSED = 1
COMPR_DCL = 3
COMPR_LINK = 0x65  # entry redirects: diskSize field holds the target fileHash

# Bitmap flags (engines/neverhood/graphics.cpp)
BF_RLE = 1
BF_HAS_DIMENSIONS = 2
BF_HAS_POSITION = 4
BF_HAS_PALETTE = 8
BF_HAS_IMAGE = 16


def hash_name(file_hash: int) -> str:
    """Loose-file base name for a resource, exactly as the engine builds it
    in ResourceMan::loadUpscaledResource: Common::String::format("%08X", hash).
    Uppercase hex, zero padded to 8 chars."""
    return "%08X" % (file_hash & 0xFFFFFFFF)


# ---------------------------------------------------------------------------
# PKWare DCL explode (decompression)
# ---------------------------------------------------------------------------

class DCLError(Exception):
    pass


class _MaxTreeBits:
    pass


def _max_bits(tree):
    return max(n for (n, _c) in tree)


_LEN_MAXBITS = _max_bits(LENGTH_TREE)
_DIST_MAXBITS = _max_bits(DISTANCE_TREE)
_ASCII_MAXBITS = _max_bits(ASCII_TREE)


def dcl_explode(data: bytes, expected_size: Optional[int] = None,
                max_out: Optional[int] = None) -> bytes:
    """Decompress a PKWare DCL 'implode' stream.

    Mirrors Common::DecompressorDCL::unpack() in common/dcl.cpp:
      byte 0: mode, 0 = binary literals (raw 8 bits), 1 = ascii (Huffman) literals
      byte 1: dictionary type 4/5/6 -> 1024/2048/4096-byte window
      then an LSB-first bit stream of (flag, literal | length+distance) items;
      length 519 terminates the stream.

    expected_size: stop once this many bytes are produced (engine behavior:
    decompression is bounded by the entry's uncompressed size).
    max_out: optional early-exit for partial decodes (e.g. header peeking).
    """
    if len(data) < 2:
        raise DCLError("stream too short")

    mode = data[0]
    dict_type = data[1]
    if mode not in (0, 1):
        raise DCLError("bad literal mode 0x%02X" % mode)
    if dict_type not in (4, 5, 6):
        raise DCLError("bad dictionary type 0x%02X" % dict_type)

    limit = expected_size
    if max_out is not None and (limit is None or max_out < limit):
        limit = max_out

    out = bytearray()
    bitbuf = 0
    bitcnt = 0
    pos = 2
    n = len(data)
    length_tree = LENGTH_TREE
    distance_tree = DISTANCE_TREE
    ascii_tree = ASCII_TREE

    def need(k):
        nonlocal bitbuf, bitcnt, pos
        while bitcnt < k:
            if pos >= n:
                raise DCLError("unexpected end of stream")
            bitbuf |= data[pos] << bitcnt
            pos += 1
            bitcnt += 8

    def bits(k):
        nonlocal bitbuf, bitcnt
        if k == 0:
            return 0
        need(k)
        v = bitbuf & ((1 << k) - 1)
        bitbuf >>= k
        bitcnt -= k
        return v

    def huff(tree, maxbits):
        nonlocal bitbuf, bitcnt
        code = 0
        nb = 0
        while nb < maxbits:
            code |= bits(1) << nb
            nb += 1
            sym = tree.get((nb, code))
            if sym is not None:
                return sym
        raise DCLError("bad Huffman code")

    while limit is None or len(out) < limit:
        if bits(1):  # (length, distance) pair
            value = huff(length_tree, _LEN_MAXBITS)
            if value < 8:
                token_length = value + 2
            else:
                token_length = 8 + (1 << (value - 7)) + bits(value - 7)
            if token_length == 519:
                break  # end-of-stream marker
            value = huff(distance_tree, _DIST_MAXBITS)
            if token_length == 2:
                token_offset = (value << 2) | bits(2)
            else:
                token_offset = (value << dict_type) | bits(dict_type)
            token_offset += 1
            if token_offset > len(out):
                raise DCLError("copy before start of output")
            # overlapping copy, byte by byte semantics
            start = len(out) - token_offset
            for i in range(token_length):
                out.append(out[start + i])
        else:  # literal
            if mode == 1:
                out.append(huff(ascii_tree, _ASCII_MAXBITS))
            else:
                out.append(bits(8))

    return bytes(out)


# ---------------------------------------------------------------------------
# BLB archive (engines/neverhood/blbarchive.cpp)
# ---------------------------------------------------------------------------

BLB_ID1 = 0x2004940
BLB_ID2 = 7


@dataclass
class BlbEntry:
    archive: "BlbArchive"
    index: int
    file_hash: int
    type: int
    compr_type: int
    time_stamp: int
    offset: int
    disk_size: int
    size: int
    ext_data: Optional[bytes] = None

    @property
    def type_name(self) -> str:
        return RES_TYPE_NAMES.get(self.type, "unknown(%d)" % self.type)

    @property
    def is_link(self) -> bool:
        return self.compr_type == COMPR_LINK

    @property
    def link_target(self) -> int:
        # ResourceMan::findEntry: for comprType 0x65 entries, the diskSize
        # field holds the fileHash of the entry this one redirects to.
        return self.disk_size


class BlbArchive:
    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.entries: list[BlbEntry] = []
        self._fd = open(path, "rb")
        self._parse()

    def close(self):
        self._fd.close()

    def _parse(self):
        fd = self._fd
        hdr = fd.read(16)
        if len(hdr) != 16:
            raise ValueError("%s: truncated header" % self.path)
        id1, id2, ext_data_size, file_size, file_count = struct.unpack(
            "<IHHiI", hdr)
        real_size = os.fstat(fd.fileno()).st_size
        if id1 != BLB_ID1 or id2 != BLB_ID2 or file_size != real_size:
            raise ValueError("%s seems to be corrupt (id1=%08X id2=%d "
                             "fileSize=%d real=%d)" %
                             (self.path, id1, id2, file_size, real_size))

        hashes = struct.unpack("<%dI" % file_count, fd.read(4 * file_count))

        records = fd.read(20 * file_count)
        ext_offsets = []
        for i in range(file_count):
            (typ, compr, ext_ofs, ts, ofs, disk_size, size) = struct.unpack_from(
                "<BBHIIII", records, i * 20)
            self.entries.append(BlbEntry(
                archive=self, index=i, file_hash=hashes[i], type=typ,
                compr_type=compr, time_stamp=ts, offset=ofs,
                disk_size=disk_size, size=size))
            ext_offsets.append(ext_ofs)

        if ext_data_size > 0:
            ext_data = fd.read(ext_data_size)
            for i, entry in enumerate(self.entries):
                # blbarchive.cpp: extData + extDataOffsets[i] - 1 (1-based)
                if ext_offsets[i] > 0:
                    entry.ext_data = ext_data[ext_offsets[i] - 1:]

    def read_raw(self, entry: BlbEntry) -> bytes:
        self._fd.seek(entry.offset)
        return self._fd.read(entry.disk_size)

    def load(self, entry: BlbEntry, max_out: Optional[int] = None) -> bytes:
        """Load and (if needed) decompress an entry, like BlbArchive::load."""
        if entry.compr_type == COMPR_UNCOMPRESSED:
            self._fd.seek(entry.offset)
            n = entry.disk_size
            if max_out is not None:
                n = min(n, max_out)
            return self._fd.read(n)
        elif entry.compr_type == COMPR_DCL:
            raw = self.read_raw(entry)
            return dcl_explode(raw, expected_size=entry.size, max_out=max_out)
        else:
            raise ValueError("unknown compression type %d for %s" %
                             (entry.compr_type, hash_name(entry.file_hash)))


class ResourceSet:
    """Merged view over several archives, mirroring ResourceMan:
    the entry with the highest timeStamp wins for a given hash, and
    comprType 0x65 link entries are followed to their target."""

    def __init__(self):
        self.archives: list[BlbArchive] = []
        self.entries: dict[int, BlbEntry] = {}
        self.shadowed = 0  # entries hidden by a newer timestamp

    def add_archive(self, path: str) -> BlbArchive:
        archive = BlbArchive(path)
        self.archives.append(archive)
        for entry in archive.entries:
            existing = self.entries.get(entry.file_hash)
            if existing is None:
                self.entries[entry.file_hash] = entry
            elif entry.time_stamp > existing.time_stamp:
                self.entries[entry.file_hash] = entry
                self.shadowed += 1
            else:
                self.shadowed += 1
        return archive

    def find(self, file_hash: int) -> Optional[BlbEntry]:
        """findEntry(): follow 0x65 link chains to the real data entry."""
        entry = self.entries.get(file_hash)
        seen = set()
        while entry is not None and entry.is_link:
            if entry.file_hash in seen:
                return None  # broken link cycle
            seen.add(entry.file_hash)
            entry = self.entries.get(entry.link_target)
        return entry

    def ext_data(self, file_hash: int) -> Optional[bytes]:
        """queryResource(): extData always comes from the FIRST entry
        (the one found for the hash itself, before following links)."""
        entry = self.entries.get(file_hash)
        return entry.ext_data if entry else None

    def load(self, file_hash: int, max_out: Optional[int] = None) -> Optional[bytes]:
        entry = self.find(file_hash)
        if entry is None:
            return None
        return entry.archive.load(entry, max_out=max_out)

    def close(self):
        for archive in self.archives:
            archive.close()


# ---------------------------------------------------------------------------
# Bitmap resources (engines/neverhood/graphics.cpp)
# ---------------------------------------------------------------------------

@dataclass
class Bitmap:
    flags: int
    rle: bool
    width: int
    height: int
    position: Optional[tuple]      # (x, y) if BF_HAS_POSITION
    palette: Optional[bytes]       # 1024 bytes RGBX (4 bytes/entry) or None
    pixels: Optional[bytes]        # packed pixel data (RLE or padded rows)


def parse_bitmap_resource(data: bytes) -> Bitmap:
    """Python port of parseBitmapResource()."""
    pos = 0
    flags = struct.unpack_from("<H", data, pos)[0]
    pos += 2

    rle = bool(flags & BF_RLE)

    if flags & BF_HAS_DIMENSIONS:
        width, height = struct.unpack_from("<HH", data, pos)
        pos += 4
    else:
        width, height = 1, 1

    position = None
    if flags & BF_HAS_POSITION:
        position = struct.unpack_from("<HH", data, pos)
        pos += 4

    palette = None
    if flags & BF_HAS_PALETTE:
        palette = data[pos:pos + 1024]
        pos += 1024

    pixels = None
    if flags & BF_HAS_IMAGE:
        pixels = data[pos:]

    return Bitmap(flags=flags, rle=rle, width=width, height=height,
                  position=position, palette=palette, pixels=pixels)


def unpack_sprite_rle(source: bytes, width: int, height: int) -> bytearray:
    """Python port of unpackSpriteRle() (no flipping, no color replace).
    Unfilled pixels stay 0 (the transparent index)."""
    dest = bytearray(width * height)
    src = 0
    row = 0
    # rows/chunks are signed int16 in the engine
    rows, chunks = struct.unpack_from("<hh", source, src)
    src += 4
    while True:
        if chunks == 0:
            row += rows
        else:
            for _ in range(rows):
                base = row * width
                for _ in range(chunks):
                    skip, copy = struct.unpack_from("<HH", source, src)
                    src += 4
                    dest[base + skip:base + skip + copy] = source[src:src + copy]
                    src += copy
                row += 1
        rows, chunks = struct.unpack_from("<hh", source, src)
        src += 4
        if rows <= 0:
            break
    return dest


def unpack_sprite_normal(source: bytes, width: int, height: int) -> bytearray:
    """Python port of unpackSpriteNormal(): rows are padded to 4 bytes."""
    source_pitch = (width + 3) & ~3
    dest = bytearray(width * height)
    for y in range(height):
        dest[y * width:(y + 1) * width] = source[y * source_pitch:
                                                 y * source_pitch + width]
    return dest


# ---------------------------------------------------------------------------
# Animation resources (engines/neverhood/resource.cpp, AnimResource::load)
# ---------------------------------------------------------------------------

@dataclass
class AnimFrame:
    frame_hash: int
    counter: int
    x: int
    y: int
    width: int
    height: int
    delta_x: int
    delta_y: int
    sprite_data_offs: int


@dataclass
class AnimInfo:
    anim_list_count: int
    anim_info_start_ofs: int
    sprite_data_ofs: int
    palette_data_ofs: int
    # per anim-list item: (fileHash, frameCount, frameListStartOfs)
    anim_list: list = field(default_factory=list)


def parse_anim_header(data: bytes) -> AnimInfo:
    anim_list_count, anim_info_start_ofs = struct.unpack_from("<HH", data, 0)
    sprite_data_ofs, palette_data_ofs = struct.unpack_from("<II", data, 4)
    info = AnimInfo(anim_list_count, anim_info_start_ofs,
                    sprite_data_ofs, palette_data_ofs)
    pos = 12
    for _ in range(anim_list_count):
        file_hash = struct.unpack_from("<I", data, pos)[0]
        frame_count, frame_list_start_ofs = struct.unpack_from("<HH", data, pos + 4)
        info.anim_list.append((file_hash, frame_count, frame_list_start_ofs))
        pos += 8
    return info


def anim_header_size(data: bytes) -> int:
    """Bytes needed to read the anim list given a >=12-byte prefix."""
    anim_list_count = struct.unpack_from("<H", data, 0)[0]
    return 12 + 8 * anim_list_count


def parse_anim_frames(data: bytes, info: AnimInfo, file_hash: int) -> Optional[list]:
    """Frame list for one hash of an animation resource (32 bytes/frame)."""
    for anim_hash, frame_count, frame_list_start_ofs in info.anim_list:
        if anim_hash == file_hash:
            break
    else:
        return None
    frames = []
    pos = info.anim_info_start_ofs + frame_list_start_ofs
    for _ in range(frame_count):
        (frame_hash, counter, x, y, width, height, delta_x, delta_y) = \
            struct.unpack_from("<IHHHHHhh", data, pos)
        sprite_data_offs = struct.unpack_from("<I", data, pos + 28)[0]
        frames.append(AnimFrame(frame_hash, counter, x, y, width, height,
                                delta_x, delta_y, sprite_data_offs))
        pos += 32
    return frames


# ---------------------------------------------------------------------------
# Palette helpers
# ---------------------------------------------------------------------------

def rgbx_to_rgb(palette_1024: bytes) -> list:
    """1024-byte RGBX palette -> [(r, g, b)] * 256.
    Screen::updatePalette reads bytes i*4+0..2 as R, G, B."""
    return [(palette_1024[i * 4], palette_1024[i * 4 + 1],
             palette_1024[i * 4 + 2]) for i in range(256)]


GRAYSCALE_PALETTE = [(i, i, i) for i in range(256)]
