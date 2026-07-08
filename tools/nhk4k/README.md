# The Neverhood 4K — tooling (nhk4k)

Continuation of sergio256256's `scummvm_neverhood_4k` fork (branch `n4k`,
our work on `n4k-continue`). Goal: a pipeline where a user supplies their
own original game files and everything needed for 4K playback is generated
locally on their machine. **No game assets are ever committed to this
repository.**

## How the engine replacement contract works

The fork renders at `640*dividend/divisor x 480*dividend/divisor`
(config in `neverhood.ini` next to the BLB files; we use 4/1 = 2560x1920).
For every resource it first looks for a *loose* file under
`<looseDataFolder>` (relative to the game data dir):

```
loose_x/images/XXXXXXXX.png          still bitmap, RGBA, pre-upscaled
loose_x/images/XXXXXXXX-000.png ...  animation frames
loose_x/videos/XXXXXXXX.ogv          Theora video, yuv420p ONLY
```

`XXXXXXXX` is the resource's 8-hex file hash (uppercase). Important
engine behaviors discovered the hard way:

- There is **no fallback for videos** — a missing `.ogv` crashes.
  Unreplaced bitmaps draw garbled at factors > 1. A complete set is
  mandatory.
- Fullscreen 320x240 cutscenes must be rendered at **2x the factor**
  (2560x1920 at 4x): the engine blits replacement frames 1:1 and the
  original engine pixel-doubled these.
- ScummVM's Theora decoder accepts **YUV 4:2:0 only**.
- Sprites: palette index 0 = transparent; animation palette index 64 is
  the engine's runtime-replaced shadow color (`setRepl(64,0)`) — bake it
  as translucent black.
- Many bitmaps have no embedded palette; the scene assigns one at
  runtime (see `palette_map.py`).

## Scripts (Python 3.12; Pillow, and for AI: torch+CUDA, spandrel)

| Script | Purpose |
|---|---|
| `extract_manifest.py` | Walk BLBs, emit manifest.json + scope stats (2748 resources: 1379 bitmaps, 551 videos, 369 anims / 6793 frames). |
| `extract_assets.py` | Extract by `--hash`/`--type` to PNG (engine-accurate palettes/transparency/shadow) and raw SMK. |
| `nhk4k_lib.py` | BLB parsing, PKWare DCL explode, bitmap/anim decoding. |
| `palette_map.py` | Mine engine sources + neverhood.dat for the runtime palette of palette-less bitmaps. |
| `rebake_palettes.py` | Re-extract those bitmaps with true colors. |
| `build_loose_data.py` | One-shot originals -> complete loose set (Lanczos images + ffmpeg2theora videos). The baseline/fallback path. |
| `ai_upscale.py` | Batch AI upscale (any spandrel model) with edge color-bleed, AI alpha, inline verification. |
| `ai_upscale_videos.py` | SMK -> frames -> Real-ESRGAN animevideov3 -> ffmpeg2theora, per-video verification + fallback. |
| `upscale_wrap.py` / `coverage_report.py` / `coordinate_sites.md` | Coordinate UPSCALE macro automation; coverage is 100% as of `coverage_after.md`. |

## Rebuild-from-scratch order

1. `extract_manifest.py` (optional, stats)
2. `extract_assets.py --type bitmap` / `--type animation` / `--type video`
3. `palette_map.py` then `rebake_palettes.py --factor 1 --images <staging>`
4. `ai_upscale.py --model <weights> --src <staging> --out <out4x>`
   (or `build_loose_data.py` for fast Lanczos placeholder quality)
5. `ai_upscale_videos.py`
6. Point `neverhood.ini` `looseDataFolder` at the result.

## Encoder gotchas (hard-won)

- MSYS2 libtheora 1.2.0's encoder emits corrupt bitstreams on
  multi-frame streams; use standalone `ffmpeg2theora` 0.29 via a
  yuv4mpeg pipe. It segfaults on exit AFTER writing a valid file —
  judge success by the output, not the exit code.
- realesrgan-ncnn-vulkan can lose the Vulkan device under aggressive
  `-j` threading and then silently writes black frames — always verify
  outputs (flat-image check vs source).

## Build (MSYS2/MinGW64)

```
pacman -S mingw-w64-x86_64-toolchain make mingw-w64-x86_64-{SDL2,libtheora,libvorbis,libogg,libpng,zlib,libjpeg-turbo,pkgconf}
./configure --disable-all-engines --enable-engine=neverhood
make -j$(nproc)
```

Run with the game data dir as `--path`, plus `--extrapath` containing
`neverhood.dat` (from `dists/engine-data/`) and the theme fonts.

## Engine fixes on n4k-continue

- 32bpp screen format + Theora decoder output format (was CLUT8
  mismatch: garbled videos).
- 64-bit crash in `AnimResource::draw` (32-bit truncated pointer
  difference for upscaled frames — sergio's `TODO: 64 bit`).
- Complete UPSCALE coordinate coverage (1336/1336 sites), including
  Scene1307 key offsets and the projector slot divisor puzzle bugs.
