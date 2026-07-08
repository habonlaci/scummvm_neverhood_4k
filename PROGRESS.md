# Neverhood 4K — progress log (branch n4k-continue)

## 2026-07-08/09 — Phase 1: foundation session

**Working end-to-end:** the game runs at 2560x1920 with a complete
AI-upscaled replacement set (images: RealESRGAN x4plus-anime after a
bake-off of 11 models; videos: animevideov3). See tools/nhk4k/README.md
for the pipeline.

Done:
- Fork built (MSYS2/MinGW64, zero source changes needed), game detected
  and playable; official ScummVM installed for SD comparison
  (`run_neverhood_sd.cmd`; 4K via `run_neverhood.cmd [anime|lanczos]`).
- BLB extraction/manifest tooling; full scope measured (2748 resources,
  6793 anim frames, 551 videos).
- Palette association for 840/1081 palette-less bitmaps mined from
  engine sources + neverhood.dat (menu text, cursors fixed).
- Complete loose_4k generation: images (Lanczos fallback + AI set) and
  all 551 videos (ffmpeg2theora pipeline; fullscreen cutscenes at 8x).
- Engine fixes: 32bpp screen/Theora format, 64-bit upscaled-anim crash,
  100% UPSCALE coordinate coverage incl. puzzle bugs (Scene1307 keys,
  projector slot divisor).
- Artifact fixes in the bake: shadow index 64 -> translucent black,
  edge color bleed, AI-upscaled alpha.

Open items:
- 59 bitmaps with runtime-selected palettes unmapped (modules
  2200/2500/2700/2800; may show gray in late areas). 182 further
  unmapped bitmaps are unreferenced/dead assets.
- Video AI pass running; Lanczos versions remain as automatic fallback
  (backups in videos_lanczos_backup/).
- ScummVM launcher GUI theme/font warning (cosmetic only).
- Playtest of puzzle scenes after coordinate fixes.
- Later phases: model quality bake-off per asset class, SUPIR
  experiment for backgrounds, 16:9 exploration, upstreaming.
