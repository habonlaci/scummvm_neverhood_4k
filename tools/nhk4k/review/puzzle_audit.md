# Puzzle-scene coordinate audit (modules 1000/1200/1300/1400)

Date: 2026-07-09. Scope: every PuzzleMouse scene, symbol/tile/key panel, NPoint
table and static offset table that positions puzzle pieces in modules 1000,
1200, 1300, 1400 (+ _sprites). Verdicts: OK = correctly wrapped exactly once
(or correctly raw because the data is pre-upscaled), FIXED = bug corrected in
this pass.

Key fact used throughout: `DataResource` point arrays / points / hit rects are
upscaled at load time (`engines/neverhood/resource.cpp:430-470`), so values
read from `_dataResource` are already in screen space and must NOT be wrapped
again; constants mixed with them MUST be wrapped.

## Scene1005 (module1000.cpp:569-620) - text wall
- insertPuzzleMouse bounds, exit-click bounds, text draw x/y steps: all
  UPSCALE-wrapped once. OK.

## Scene1202 - TNT dummy symbol panel (module1200.cpp:367-470,
module1200_sprites.cpp:599-680)
- `kScene1202Points[18]` (module1200_sprites.cpp:599): every entry
  `{UPSCALE(x, y)}`, wrapped once; uses at :626-627/:673-674 assign raw. OK.
- Scene click bounds (module1200.cpp:440, :460): UPSCALE_X(20)/(620). OK.
- `kScene1202Table` is a permutation index table, correctly unwrapped. OK.
- AsScene1201LeftDoor `_klaymen->getX() < 100` (module1200_sprites.cpp:569):
  FIXED this pass -> `UPSCALE_X(100)` (upscaled getX vs raw literal).

## Scene1307 - key/symbol keyhole panel (module1300.cpp:793-910,
module1300_sprites.cpp:315-475)
- `_keyHolePoints` from `_dataResource` (pre-upscaled) combined with
  `UPSCALE_X/Y(15)` hit-rect margins (module1300.cpp:806-810). OK.
- `_clipRects[i].set(..., UPSCALE_Y(0), UPSCALE(640, 480))`. OK.
- `kAsScene1307KeyPoints` removal/insertion delta table
  (module1300_sprites.cpp:325): FIXED this pass - was raw game-space deltas
  added to pre-upscaled `_x/_y`; now `{UPSCALE(dx, dy)}` per entry.
- `kAsScene1307KeyXDelta = 70` / `kAsScene1307KeyYDelta = -12`
  (module1300_sprites.cpp:340-341): FIXED this pass -> `UPSCALE_X(70)` /
  `UPSCALE_Y(-12)`; they are added to pre-upscaled dataResource points in
  suMoveKey/stMoveKey (:430-431, :454-455). This shifted every key's rest
  position by ~54x9 px at 4.5x - the most likely cause of the reported
  wrong-looking symbol/key panel.
- `kAsScene1307KeyFrameIndices` and `kAsScene1307KeyDivValue = 200` are
  animation-frame interpolation data, not coordinates: correctly raw. OK.
- suMoveKey `_x = _prevX + (_deltaX * _frameIndex) / kAsScene1307KeyDivValue`:
  `_deltaX` derived from already-upscaled endpoints; no wrapping needed. OK.

## SsScene1302Fence (module1300_sprites.cpp:83-135) - flytrap fence (not a
puzzle panel but shares the module)
- `_y += 152`, `_y += 8`, `_y -= 8`: FIXED (auto). Stop condition
  `_firstY + 152` (:120): FIXED manually -> `_firstY + UPSCALE_Y(152)`
  (would otherwise stop the fence 152 raw px down instead of 152 game px).

## Scene1404 (module1400.cpp:640-700) - projector alignment room
- `_asProjector->getX() == 220` (module1400.cpp:676): FIXED this pass ->
  `UPSCALE_X(220)` (upscaled getX vs raw literal; the "projector in front of
  the keyhole" check never fired).

## AsCommonProjector (module1400_sprites.cpp:268-530) - projector puzzle
- `kAsCommonProjectorItems` base-point table (:268): `{UPSCALE(x, y)}, ...`
  wrapped once; trailing fields are indices/counts, correctly raw. OK.
- Slot math `slot * UPSCALE_X(108) + point.x` (:284, :425, :433, :467, :516):
  OK.
- `setGlobalVar(V_PROJECTOR_SLOT, (_x - point.x) / 108)` (:305): FIXED this
  pass -> `/ UPSCALE_X(108)`. Dividend is in upscaled pixels; raw divisor
  computed a slot index ~4.5x too large, corrupting V_PROJECTOR_SLOT whenever
  Klaymen raised the lever (projector teleports / puzzle unsolvable).
- suMoving `_x = _klaymen->getX() +/- 100` (:400-402): FIXED this pass ->
  `UPSCALE_X(100)`; raw 100 put the pushed projector nearly on top of Klaymen
  and broke the `_beforeMoveX == _x` arrival test.
- moveProjector lock windows `elX +/- UPSCALE_X(20)`, `point.y +
  UPSCALE_Y(10)` (:424-437): OK.

## Scene1407 - mouse maze (module1400.cpp:440-495,
module1400_sprites.cpp:603-830)
- `kScene1407MouseFloorY` (all UPSCALE_Y), `kScene1407MouseHoles` (x
  UPSCALE_X; floor/section/nextHole indices raw), `kScene1407MouseSections`
  (x1/x2 UPSCALE_X; goodHoleIndex raw): all wrapped exactly once. OK.
- Hole hit test margins `UPSCALE_X(14)`/`UPSCALE_X(36)` and reset-search
  seed distance `UPSCALE_X(640)`: OK (X macro on a y-delta at :737 is the
  established house convention; both axes share one factor).
- Reset-button rect (module1400.cpp:473-474) UPSCALE-wrapped. OK.

## Scene1405 - memory tile panel (module1400.cpp:706-775,
module1400_sprites.cpp:834-905)
- `kAsScene1405TileItemPositions[48]`: every entry `{UPSCALE(x, y)}` once;
  uses at :854-855 raw. OK.
- Sound-pan expression `(tileIndex % 8 * 4 + 4) * 25 / 8` is audio, not a
  coordinate. OK.
- Scene click bounds UPSCALE_X(20)/(620). OK.

## Non-findings / deliberately raw
- All `startAnimation`/`_newStickFrameIndex`/countdown literals: frame/timer
  values, not coordinates.
- Surface priorities (100/800/990/1100 etc.): not coordinates.
- `kAsScene1307KeySurfacePriorities`, `kScene1202FileHashes`,
  message-list hash tables: not coordinates.

Result: 7 genuine coordinate bugs found in the audited puzzle scenes, all
fixed and compiled (Scene1307 key deltas x2 sites + fence stop, Scene1404
projector check, AsCommonProjector slot divisor + push offsets, LeftDoor
klaymen check). No double-wrapping found anywhere in the audited scenes.
