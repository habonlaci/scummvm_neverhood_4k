# Release checklist — The Neverhood 4K (ScummVM fork + nhk4k tools)

Publishing model: **"paste your original game files, everything is generated
locally."** The repo and every release artifact ship code and tooling only —
the user supplies their own BLB game files and all upscaled assets are
produced on their machine.

## 1. Legal

- [ ] **No game assets in the repo or in any release.**
  - Verify no binary game blobs ever landed in history:
    `git log --stat -- '*.blb' '*.BLB' '*.smk' '*.ogv' '*.png'` (fork
    commits only: `git log --stat n4k..HEAD`).
  - Confirm `.gitignore` still excludes extraction/staging outputs, and that
    all tools refuse to write game-derived data inside the repo
    (`guard_outside_repo` in `tools/nhk4k/*.py`).
- [ ] **GPL-3.0 compliance.**
  - This is a fork of sergio256256's `scummvm_neverhood_4k` (branch `n4k`),
    itself a fork of ScummVM's GPL-3.0 codebase. Keep `COPYING` and the
    copyright headers intact; note the fork lineage in the release notes.
  - Any binary release (scummvm.exe) must link to the exact source used to
    build it (tag the commit, attach a source zip or point at the public
    repo/branch).
- [ ] **No personal data.**
  - Fork commits use a noreply author email — verify with
    `git log --format=%ae n4k..HEAD | sort -u`.
  - No hardcoded user paths: `git grep -n 'Users.apa' -- tools/` must return
    nothing (all defaults now come from `tools/nhk4k/nhk4k_paths.py`, which
    derives them from `NHK4K_ROOT` / the checkout location). Keep it that way
    in future commits.

## 2. End-user flow (what the release notes must explain)

1. **Prerequisites:** an original copy of The Neverhood (the `*.BLB` files),
   Python 3.12 with Pillow; for AI upscaling a CUDA GPU (torch + spandrel)
   or the bundled realesrgan-ncnn-vulkan (any Vulkan GPU). Without a GPU the
   Lanczos fallback still produces a complete, playable set.
2. **One command:** point `NHK4K_DATA` (or `--data`) at the folder holding
   the BLBs, then `python tools/nhk4k/upscale.py --model anime`. This
   extracts, re-bakes palettes, AI-upscales images and videos, downloads
   model weights on first use, and writes a complete `loose_<set>` folder.
3. **Launcher setup:** `run_neverhood.cmd` (4x), `run_neverhood_sd.cmd`
   (original resolution), `run_neverhood_debug.cmd` — each starts
   `scummvm.exe --path=<BLB dir> --extrapath=<dir with neverhood.dat and
   fonts>` with the matching `neverhood.ini` (`looseDataFolder`,
   `dividend/divisor`).
4. **Per-model loose sets:** each `--model` writes its own
   `loose_<model>/images`; videos are shared via a junction into every set,
   so switching looks is just repointing `looseDataFolder`.

## 3. Packaging (release zip contents)

**Must contain:**
- `scummvm.exe` (MinGW64 build, Neverhood engine only)
- Required MinGW64 DLLs (direct imports of the current build):
  `SDL2.dll`, `libjpeg-8.dll`, `libogg-0.dll`, `libpng16-16.dll`,
  `libtheoradec-2.dll`, `libvorbis-0.dll`, `libvorbisfile-3.dll`,
  `libwinpthread-1.dll`, `zlib1.dll`
  (re-verify after each rebuild: `objdump -p scummvm.exe | grep "DLL Name"`)
- `neverhood.dat` (from `dists/engine-data/`) and `fonts.dat`
- launcher scripts (`run_neverhood.cmd`, `run_neverhood_sd.cmd`,
  `run_neverhood_debug.cmd`) plus a template `neverhood.ini`
- `tools/` (the nhk4k pipeline, including `ffmpeg2theora.exe` licence note —
  users can also supply their own via `NHK4K_FFMPEG2THEORA`)

**Must never contain:**
- any `*.BLB` or other original game data
- generated loose sets (images/videos derived from game data)
- AI model weights — `upscale.py` auto-downloads them on first use

## 4. Known issues (document in release notes)

- **59 runtime-palette bitmaps** in late modules still resolve their palette
  only at runtime; they may draw with a stale palette until the scene
  assigns one (tracked via `palette_map.py`).
- **ScummVM GUI theme font warning** on startup — cosmetic only, gameplay
  rendering is unaffected.
- **Upscale factor is fixed at 4x** in the engine's coordinate tables
  (static-table initializer limitation, documented in
  `engines/neverhood/neverhood.h`). `dividend/divisor` in `neverhood.ini`
  only selects between SD (1/1) and the shipped 4x path.

## 5. Pre-release test matrix

- [ ] **Fresh-machine dry run:** clean Windows VM/user, unzip release, paste
      BLBs, run `upscale.py`, launch — no hardcoded-path or missing-DLL
      failures.
- [ ] **SD launcher** (`run_neverhood_sd.cmd`) boots and plays without a
      loose set.
- [ ] **Every shipped loose set boots** (each `--model` variant listed in
      the release notes).
- [ ] **Playtest:** intro cutscene, nursery (first room), TNT room, and the
      Scene1307 key/lock puzzle (historically the trickiest UPSCALE
      coordinate site).

## 6. Upstream etiquette

- [ ] Contact **sergio256256** before/with the release: credit the original
      `n4k` work prominently, link their repo.
- [ ] Offer the `n4k-continue` branch back (PR or patch series) — 64-bit
      fixes, full coordinate coverage, and the tooling are useful upstream
      even if they only cherry-pick.
