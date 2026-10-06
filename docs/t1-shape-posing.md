# Tribes 1 shapes: posing and visibility

How Skinner turns a Tribes 1 `.dts` into the pose and parts the game shows, as fixed in October 2026
(`tools/export_model.py`, `tools/animate_t1.py`). Each rule names the engine source it follows
(`C:\TribesRebuild`), so it can be checked against any other loader (ArenaPrototype's
`Assets/Game/MapCompatibility/Tribes/Formats/TribesShapeStaticMesh.cs` and the presentation profiles).

Found on the Tribes Ascend conversion chaingun: its barrel tip came out turned and its muzzle flash
floated beside the gun. Stock shapes had the same problems wherever their data triggered them.

## 1. Every rotation acts transposed

DarkStar multiplies row vectors: `point * matrix` (`engine/Ml/Code/m_mulf.cpp`,
`m_Point3F_TMat3F_mul`) and `world = local * parent` (`engine/Ts3/code/ts_shapeInst.cpp:444`). The
quaternion-to-matrix code is the textbook one (`engine/Ml/Code/m_quat.cpp`, `QuatF::makeMatrix`) and
`Quat16::getQuatF` flips no signs, so with column vectors every node rotation is the **transpose**
(the conjugate quaternion).

Skinner used to transpose only for the five player armors (a `PLAYER_MODEL_STEMS` list). It now does so
for every shape. 180° rotations are their own transpose, which is why most stock shapes looked right.

- ArenaPrototype: **already does this** for every shape (`TribesShapeStaticMesh.cs`, `TransformMatrix`,
  comment "Source renderer transposes the quaternion matrix").

## 2. The root node's rotation is drawn

`TS::ShapeInstance` turns node 0's default transform into `fRootDeltaTransform` and pushes it before
drawing (`ts_shapeInst.cpp:1861` and `:2600`). Skinner used to multiply by the inverse of the whole root
transform. It now removes only the root's offset (to keep models centred) and keeps its rotation. Stock
roots have identity rotation; the TA models' roots do not.

- ArenaPrototype: its node loop starts from node 0's own local transform (`worlds[nodeIndex] = local`
  for the root), so it appears to include it. Worth confirming nothing later cancels it.

## 3. Objects the game doesn't show at rest are hidden

- An object flagged `DefaultInvisible` (`flags & 1`, `engine/Ts3/Inc/ts_shape.h:230`) starts hidden:
  `fVisible = !(fFlags & DefaultInvisible)` (`ts_shapeInst.cpp:802`). These are muzzle flashes.
  Skinner had this check commented out, so every weapon showed its flash.
- A shape with a sequence named `visibility` gets a thread on it held at position 0 while the shape is
  intact (`program/code/staticBase.cpp:166-168`, `:258`; `Moveable` and `Sensor` derive from
  `StaticBase`, `explosion.cpp:229` does the same). That sequence's object tracks (key `mat_index`: `0x4000`
  = visibility key, `0x8000` = visible) override the flag. This hides the destroyed "hulk" and debris
  objects of generators, turrets, stations and sensors, and some door pieces.

Skinner: `export_model.initially_visible(shape, obj)`, also used by the GLB animation baker for every
clip that has no visibility track of its own.

- ArenaPrototype: check that `DefaultInvisible` objects and `visibility@0` are honoured for map-placed
  statics. Its deployable profile samples `visibility@1` for the destroyed state
  (`TribesDeployablePresentationProfile.cs:753`).

## 4. The pose: several sequences at once, each node from the first that animates it

The game runs several threads together, and a node takes its transform from the thread that animates it.
Skinner's still pose now layers, in this order: `activation` (end), `deploy` (end), `power` (end),
`root`, `ambient`, `idle` (start). Each node takes its pose from the first of these that has a track for
it; nodes with none keep their default transform. A **looping** sequence (`cyclic`) has no end state,
so it contributes its first keyframe. That covers the station `power` spins; turret `power`
plays once and its end is the raised turret.

Effects: turrets show raised (unpowered they sit retracted in their base), deployables show deployed
(remote stations open, the mine stands on its legs, the pulse sensor's dish up on its mast).

- ArenaPrototype: its deployable profile checks geometry at `deploy` 1 and `power` .5
  (`TrySampleGeometry` in `TribesDeployablePresentationProfile.cs`); I did not trace which positions it
  renders. Check that map-placed turrets get `power` at 1, and that nodes animated by one sequence aren't
  reset by another.

## Measured on the stock install (`C:\realstocktribe`, ~200 shapes)

108 previews unchanged, 79 changed. 36 only turned as a whole (trees, effects, some doors and displays);
10 had parts move relative to each other (ammo pad, camera, command, remote inventory, main pad, mine,
pulse sensor, remote turret, satellite dish, teleporter); 33 lost hidden parts (flashes, hulks, door pieces).
`fiery.DTS` (an explosion) has nothing visible at rest; its old preview was kept. The hellfire turret and
indoor gun stopped being twisted.

The nine weapon previews in the oldest format (chaingun, disc, energy gun, grenade launcher, mortar,
plasma, repair gun, shotgun, sniper) came from an earlier exporter that pointed weapons the other way.
They now point the same way as every other T1 weapon in the catalog, and keep their single-skin behaviour.
