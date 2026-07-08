# Neverhood 4K - game-space coordinate site catalog

Built by systematically grepping/diffing the FULLY COVERED modules
(`module1000`, `module1400`, `module2200`, `module2800` + `_sprites`) in
`engines/neverhood/modules/`. The engine renders at an upscale factor; every
game-space (640x480) integer coordinate passed by per-scene code must be
wrapped in `UPSCALE_X(x)`, `UPSCALE_Y(y)` or the pair form `UPSCALE(x, y)`
(defined in `engines/neverhood/neverhood.h:37-39`; `UPSCALE(x, y)` expands to
`UPSCALE_X(x), UPSCALE_Y(y)` - i.e. it fills TWO argument positions).

Style rule observed everywhere in the covered modules: adjacent literal
`(x, y)` argument pairs use `UPSCALE(x, y)`; a lone coordinate (or one whose
partner is a non-literal expression) uses `UPSCALE_X` / `UPSCALE_Y`.
Note the covered code treats the two axes as interchangeable when scaling
deltas (`UPSCALE_X(-120)` used on a y-delta, `module1400_sprites.cpp:346`) -
the dividend/divisor are the same for both axes.

`tools/nhk4k/upscale_wrap.py` automates every entry marked AUTO;
entries marked FLAG are detected but routed to `tools/nhk4k/review/<module>.md`.

## 1. Scene sprite insertion (constructor-signature driven)

`Scene::insertSprite<T>(args...)` forwards to `new T(_vm, args...)` and
`Scene::insertKlaymen<T>(args...)` to `new T(_vm, this, args...)`
(`engines/neverhood/scene.h:79-146`). Which argument positions are
coordinates therefore depends on T's constructor. The tool parses every
`T(NeverhoodEngine *vm, ...)` declaration in `engines/neverhood/**/*.h` and
treats parameters named `x, y, x1, y1, x2, y2, width, height` as coordinate
slots. AUTO.

- `insertKlaymen<T>(x, y[, clipRects, count])` - x,y at positions 0,1; the
  trailing rect pointer/count are NOT coordinates.
  Verified: `module1000.cpp:142` `insertKlaymen<KmScene1001>(UPSCALE(200, 433));`
  and `module2200.cpp:512` `insertKlaymen<KmScene2201>(UPSCALE(300, 427), _clipRects, 2);`
  and `module2800.cpp:1378` `insertKlaymen<KmScene2806>(UPSCALE(441, 423), false, _clipRects, 4);`
- `insertSprite<T>(...)` - positions vary per class:
  - `AsScene1201Tape(vm, scene, nameHash, surfacePriority, x, y, fileHash)`
    (`modules/module1200_sprites.h:43`): coords at call-args 3,4.
    Verified: `module2200.cpp:484` and `module2800.cpp:410`
    `insertSprite<AsScene1201Tape>(this, 7, 1100, UPSCALE(459, 432), 0x9148A011);`
  - `AsCommonKey(vm, scene, keyIndex, surfacePriority, x, y)`
    (`modules/module2200_sprites.h:93`). Verified: `module2200.cpp:735`.
  - `AsScene1001Lever(vm, scene, x, y, deltaXType)`
    (`modules/module1000_sprites.h:59`). Verified: `module1000.cpp:175`
    `insertSprite<AsScene1001Lever>(this, UPSCALE(150, 433), 1);` - trailing
    `1` is deltaXType, NOT a coordinate.
  - `AnimatedSprite(vm, fileHash, surfacePriority, x, y)` (`sprite.h:149`).
    Verified: `module2800.cpp:936` and `module2800.cpp:955`
    `insertSprite<AnimatedSprite>(hash, 100, UPSCALE(529, 326));`
- direct `new T(...)` constructor calls follow the same rule (e.g.
  `new BaseSurface(_vm, 0, UPSCALE(640, 480), "background")`,
  `module2200.cpp:1337` and `module2200.cpp:1341` - `width/height` params).

## 2. Clip rectangles

- `setClipRect(x1, y1, x2, y2)` (`sprite.h:80`) - all four args. AUTO.
  Verified: `module1000.cpp:168`
  `_klaymen->setClipRect(UPSCALE(0, 0), tempSprite->getDrawRect().x2(), UPSCALE_Y(480));`
  and `module2800.cpp:1023` `_klaymen->setClipRect(UPSCALE(0, 0), UPSCALE(560, 315));`
  The 1-arg overloads `setClipRect(NRect&)` / `(NDrawRect&)` take runtime
  rects - never touched (arg count gate).
- `NRect::set(x1, y1, x2, y2)` - only matched when the receiver name contains
  "rect". AUTO. Verified: `module2200.cpp:745-746`
  `_leftDoorClipRect.set(_ssSmallLeftDoor->getDrawRect().x, UPSCALE_Y(0), UPSCALE(640, 480));`
- NRect / NDrawRect / NPoint field assignments with literal RHS:
  `.x1/.y1/.x2/.y2/.x/.y/.width/.height = N;`. AUTO.
  Verified: `module2200.cpp:492-495` `_clipRects[0].y1 = UPSCALE_Y(0); ...`
  and `module2800.cpp:691-699`; `module2200.cpp:1438` `sourceRect.width = UPSCALE_X(640);`

## 3. Mouse regions

- `insertPuzzleMouse(fileHash, x1, x2)` (`scene.h:67`) - args 1,2 are X
  bounds, arg 0 is a hash. AUTO. Verified: `module1000.cpp:578` and
  `module2200.cpp:609` `insertPuzzleMouse(0x00A08089, UPSCALE_X(20), UPSCALE_X(620));`
- `insertScreenMouse(hash[, NRect*])` / `insertNavigationMouse(hash, type)` -
  NO literal coordinates; not in catalog.

## 4. Surfaces

- `createSurface(surfacePriority, width, height)` (`sprite.h:99`,
  `Background` has same shape) - args 1,2. Arg 0 is PRIORITY - never scale.
  AUTO. Verified: `module1400_sprites.cpp:30` `createSurface(900, UPSCALE(152, 152));`
  and `module2200.cpp:1328` `_background->createSurface(0, UPSCALE(640, 528));`
  Look-alike: `createSurface1(fileHash, surfacePriority)` (`sprite.h:185`) -
  no coordinates.
- `BaseSurface(vm, priority, width, height, name)` - see section 1.
- `drawString(destSurface, x, y, string[, len])` (`graphics.h:146`) - args
  1,2. AUTO. Verified: `module1000.cpp:613` and `module2200.cpp:1456`.
- `copyFrom/copyFromWithAlpha(surface, x, y, sourceRect)` (`graphics.h:97-98`)
  - args 1,2. AUTO. Verified: `module2200.cpp:1440` and `module2200.cpp:1446`.
- `loadSprite(fileHash, flags, surfacePriority, x, y)` (`sprite.h:128`) -
  args 3,4 only when present. AUTO (5-arg form). Verified:
  `module1000_sprites.cpp:398` `loadSprite(0x1052370F, kSLFDefDrawOffset | kSLFSetPosition, 800, UPSCALE(526, ...));`

## 5. Sprite position members and locals

- `_x = N; _y = N;` (also `+=`, `-=`, `_newX`, `_newY`). AUTO.
  Verified: `module1400_sprites.cpp:31-32` `_x = UPSCALE_X(454); _y = UPSCALE_Y(217);`
  and `module1000_sprites.cpp:429` `_y -= UPSCALE_Y(8);`
- `_x = <expression>;` - FLAG (double-scaling risk). Common benign case:
  `_x = x;` constructor passthrough (caller already scaled) - do NOT wrap.
- `setX(N) / setY(N)`. AUTO. Verified: `module2800.cpp:1024-1025`
  `_klaymen->setX(UPSCALE_X(560)); _klaymen->setY(UPSCALE_Y(315));`
- local `int16 x/y = N;` and `x/y += N;` (text drawing cursors). AUTO.
  Verified: `module1000.cpp:607` `int16 y = UPSCALE_Y(36);` and
  `module1000.cpp:614` `y += UPSCALE_Y(36);`

## 6. Coordinate comparisons and deltas

- Relational/equality comparison of a coordinate expression
  (`getX()`, `getY()`, `_x`, `_y`, `_newX`, `_newY`, `<expr>.x/.y/.x1/...`,
  `param.asPoint().x/.y`) against an integer literal - wrap the literal.
  AUTO (literal 0 skipped: no-op). Verified: `module1000.cpp:342`
  `_klaymen->getY() > UPSCALE_Y(230)` and `module2200.cpp:903`
  `_klaymen->getX() > UPSCALE_X(85)`; `module2800.cpp:573`
  `param.asPoint().x <= UPSCALE_X(20)`.
- Additive deltas on coordinate expressions (`getX() - 20`, `_x + 15`) -
  FLAG only (the other operand must be verified to be in scaled space).
  Verified wrapped precedent: `module1000_sprites.cpp:462`
  `_x = ((Sprite*)sender)->getX() - UPSCALE_X(98);` and
  `module1000_sprites.cpp:830` `_x - UPSCALE_X(15) < _klaymen->getX()`.

## 7. Static coordinate tables

- `static const NPoint name[] = { {x, y}, ... }` - each entry becomes
  `{UPSCALE(x, y)}`. AUTO. Verified: `module2200_sprites.cpp:116-118`
  (`kSsScene2202PuzzleCubePoints`) and `module2800_sprites.cpp:299-303`
  (`kAsScene2804CrystalWavesPoints`).
- `static const int16 name[] = {...}` where the NAME encodes the axis
  (contains `X` xor `Y`): each element wrapped with that axis. AUTO.
  Verified: `module2200.cpp:943` `kScene2206XPositions[] = { UPSCALE_X(384), ... }`,
  `module1400_sprites.cpp:604` `kScene1407MouseFloorY[] = { UPSCALE_Y(106), ... }`,
  `module2200_sprites.cpp:446` `kAsScene2206DoorSpikesXDeltasOpen[] = { UPSCALE_X(-24), ... }`.
  If the name has no axis (or both) - FLAG.
- Struct tables embedding NPoint/coords (e.g. `kAsScene1404ProjectorItems`,
  `module1400_sprites.cpp:269-273` `{UPSCALE(154, 453), 4, 2, 0, 0, 1}`:
  only the leading NPoint is scaled; the trailing ints are indices/flags) -
  FLAG whole table for manual wrapping.
- `setRectList(0x004B...)` takes a DATA-RESOURCE HASH, not a table pointer -
  never scale (see look-alikes). Rect lists from resources are pre-scaled at
  load, as are `_dataResource.getPoint()` results (see section 9).

## 8. Messages

- `sendMessage` params are usually enums/counts/hashes, but a few carry
  coordinates, e.g. `sendMessage(_asElevator, 0x2000, UPSCALE_Y(480));`
  (`module2200.cpp:1178` and `module2200.cpp:1215`). FLAG: any `sendMessage`
  with a bare non-zero/non-one integer 3rd arg is routed to review; never
  auto-wrapped.
- Klaymen walk targets received via message list params are wrapped at the
  receiver: `startSpecialWalkRight(UPSCALE_X(param.asInteger()));`
  (`module2200_sprites.cpp:949` and `module2200_sprites.cpp:1021`). The walk
  helpers (`startWalkToX`, `startSpecialWalkLeft/Right`,
  `startWalkToXDistance`) take X coordinates at arg 0 - AUTO for literals,
  FLAG for `param.asInteger()`-style expressions.

## 9. LOOK-ALIKES - must NOT be scaled

- `insertStaticSprite(fileHash, surfacePriority)` - hash + PRIORITY.
  e.g. `module2800.cpp` (`insertStaticSprite(0x1A4D4120, 1100)`); priorities
  like 100/800/900/1100 look like coordinates but never scale.
- `createScene(sceneNum, which)`, `createNavigationScene(hash, which[,
  types])`, `createSmackerScene(hash, flags...)` - ids/hashes/flags.
- `setBackground/setPalette/setMessageList/setMessageList2/setRectList/
  playSound/startMusic` - 32-bit hashes (`0x...`). A hex literal in a
  coordinate slot is flagged, never wrapped.
- `startAnimation(fileHash, frameIndex, frameIndex)` - FRAME INDICES.
- `createSurface(<priority>, w, h)` first argument; `setSurfacePriority(N)`.
- `setRepl(64, 0)` - palette replacement indices.
- `setDoDeltaX(0/1)`, delta counts, `_countdown = N` (timer ticks),
  `SetSpriteUpdate(&...)` (callback pointer - despite the name, no coords).
- Constructor look-alikes: `SsCommonButtonSprite(vm, scene, hash, priority,
  soundHash)` (`modules/module1000_sprites.h:67`) - the `100, 0` in
  `module1000.cpp:180` are priority + sound hash; `Scene1501(vm, parent,
  bgHash, sndHash, countdown2, countdown3)` - `150, 48` in `module1500.cpp`
  are TIMERS.
- Sound parameters: `setSoundListParams(list, bool, 50, 600, 10, 150)`,
  volumes 0-100, pan/countdown values.
- Values already in scaled space at runtime - wrapping would double-scale:
  - `pt.x / pt.y` from `_dataResource.getPoint(...)` (resources pre-scaled;
    `module2800.cpp:725-726` passes them to `insertKlaymen` unwrapped),
  - `getX()/getY()`, `getDrawRect().x2()`, `getClipRect()` results,
  - constructor params `x`/`y` inside sprite implementations (`_x = x;`),
  - anything already containing `UPSCALE`.

## 10. Known residual misses in the "fully covered" modules

Found by running upscale_wrap.py --dry-run on the covered modules (kept as
findings, engines/ untouched): `module1400.cpp:676` (`getX() == 220`),
`module2200.cpp:1262` (`getY() == 423`), `module2200.cpp:1675`
(`kScene2247XPositions {513, 602}` unwrapped), `module2800.cpp:735/738`
(`setClipRect(0, 25, ...)`), `module2800.cpp:2109/2120/2143` (Scene2822
background scroll Y offsets), `module2800_sprites.cpp:135/926`
(`_y = -276`), `module1000_sprites.cpp:267` (`setClipRect(0, ...)`, no-op).
