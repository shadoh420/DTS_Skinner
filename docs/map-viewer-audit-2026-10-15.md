# Map viewer gap audit — 2026-10-15

Scope: every Maps page in Skinner. That is 8 viewer codebases serving about 30 game pages. The audit covers weather, fog, sky,
lighting, animated materials, effects, movers, entities and sound.

- **Checkout:** `C:\tmp\DTS_Skinner-texture-workshop`, branch `claude/skinner-sky`, HEAD `ef5c732`. `static/t1-maps/skies.js` and `viewer.js`
  have uncommitted changes (the Skinner Sky preset group). No gap below comes from that work.
- **Method:** this was read only. Five agents worked in parallel, one per viewer family. Each read the viewer, its importer, the docs and
  the memory notes. Each also scanned the installed game data and the imported library (`%LOCALAPPDATA%\DTS-Skinner\local-data`)
  to count what the source carries. The new high-impact claims were spot-checked against the code (marked ✔︎).
  The rest are agent-reported. The read-only counting scripts are kept in
  `C:\tmp\skinner-evidence\map-viewer-audit-2026-10-15\`.
- **Relation to the 2026-10-09 repo audit:** that audit covered repo health (license, CI, CRLF, disk space) and had no
  map-rendering rows. This is the first gap audit of the viewers, and it supersedes nothing.
- **Not done:** no game runs, imports, test suites or servers. So the visual impact of each gap is inferred from code and data
  counts, not compared with the game.
- **Severity bar:** this follows *parity-good-enough*. The audit flags structural gaps only: an effect missing entirely, data discarded,
  or the wrong model. Tuning within about 10–15% of the game is not flagged.
- **Labels:**
  - **New**: not in any doc, memory note or importer gap list.
  - **Known**: already recorded somewhere (the source is cited).
  - **Declined**: the user chose to skip it earlier. It is listed so nobody reopens it without asking.
- **Effort:** S is up to a few hours, M is about a day, L is several days.

## Outcome (2026-10-17)

Every gap below was worked by one fix session per viewer on 2026-10-15–17 (PRs #87–#132, all merged), then checked against the games.
- **Out of scope** (the user's call): baked lighting, sound and ambience, and Doom 3 stencil shadows.
- **Detail:** the rows below keep their original audit text. This section records what happened to them. Each PR body has the per-item detail and measurements.
- **Done** means implemented and merged; **left** means deferred, with its reason in the PR named.

| Viewer | PRs | Done | Left (reason in the PR) |
|---|---|---|---|
| T1 family | #87, #89, #90, #102, #104, #106, #111 | <ul><li>Haze model, alpha, sky uniforms</li><li>Power and IFL animation</li><li>ES2 night and shading</li><li>Starsiege SNOW, units and turrets (#104; the real library was re-imported on 10-16)</li><li>RB3D: Glide sky, clouds, sun, seasons, dusk and night</li></ul> | <ul><li>RB3D night approximate (sky dark, ground 1.6× bright)</li><li>RB3D cloud shadows, trees and scatter, moon and stars</li><li>T1 Raindance game comparison</li></ul> |
| Tribes 2 | #114, #117, #120 | <ul><li>Precipitation (speed game-checked at ~290 fps), lightning, fireballs, emitters</li><li>Interior light states, lava overlay, vehicles</li><li>Horizontal FOV</li><li>Water culled to fluid blocks</li></ul> | <ul><li>`renderBottomTexture` (the upstream sky fogs below the horizon)</li><li>`hazeDistance` (not a V12 field)</li><li>Underwater tint not limited to fluid blocks</li></ul> |
| Q3 family | #115, #119, #122, #125, #126 | <ul><li>Light grid (live since the #122 rewrite fix)</li><li>PVS, sky depth, tcGen vector, JA racks, fog (JA fog kept by the user), far clip, autosprite</li><li>Flares, movers, sky portals, weather, sun, pickups</li><li>CNQ3 gamma</li><li>JK2/JA fx_runner and RTCW/ET smoke particles</li></ul> | <ul><li>Lightstyles, portal and mirror surfaces, surface sprites</li><li>Area portals declined (no pixel change)</li><li>RTCW portal fog 1.55×, JA heavyrainfog 0.845, ET rain 1.175</li></ul> |
| Unreal family | #110, #112, #113, #118, #123, #124, #127, #129–#132 | <ul><li>Per-frame hook, fog, emissive, animated textures, sky motion, light styles, Source 3D skies, underwater tint</li><li>Coronas (227 rule), Quake/Q2 items and torches, ropes, decals, env and detail textures, mirrors, movers at start keys, VTMB prop light</li><li>E.Y.E. face winding</li><li>Quake fence cut-outs</li><li>Source dust and steam, E.Y.E. env_fire</li><li>Bloodlines particles, rain and rain boxes</li></ul> | <ul><li>UE1 SmokeGenerators (triggered only), 227 XEmitters, UE2/UE3 particles</li><li>Triggered movers, spotlight beams</li><li>NaliC2 start 1.48× bright</li></ul> |
| Doom 3 + Quake 4 | #92–#101, #103, #121 | <ul><li>D3 sky and RoE portal sky</li><li>decal_macro, sort, shear, parallel lights</li><li>Deform particles</li><li>Animated props posed at frame 1</li><li>Game-checked within ~15%</li></ul> | <ul><li>101 env_swinglight lamps in bind pose (admin lobby 0.72)</li><li>Fog and blend lights: still the open decision below</li></ul> |
| Reflex | #88, #96, #105 | <ul><li>Shootables and turrets</li><li>Skinned poses</li><li>Spot shadows and godrays</li><li>Weapon colours, spin and bob</li></ul> | <ul><li>Aerowalk bright wall</li><li>Lights and particles on animated bones</li><li>Model normal maps</li></ul> |
| Diabotical | #91, #109 | <ul><li>Sky</li><li>Unlit materials, prop shadows</li><li>Fog and vertical fog</li><li>Beams, flicker, LUT, movers, liquids, surface detail</li><li>Within 1–2% of the game</li></ul> | <ul><li>b_icefall 22% dark</li><li>Bioplant shaft 1.7–2× bright</li><li>Volume lights, triggered props</li></ul> |
| N64 | #116 | 69 EnvMapping materials as matcaps | — (fog, combiners and the rest aren't in the OBJ data) |

**Section 5 decisions:**
1. **D3 fog and blend lights:** still open.
2. **Sound:** out of scope.
3. **Re-imports:** done for every affected library.
4. **Starsiege units:** yes, drawn behind the NPC toggle.

## Viewer families

| Viewer | Pages it serves |
|---|---|
| `static/t1-maps/viewer.js` + `skies.js` | Tribes 1, Earthsiege 1/2, Starsiege, Red Baron 3D |
| `static/t2-maps/skinner` (built from `map-client/` + exogen/t2-mapper `e9b6332`) | Tribes 2, T2RPG |
| `static/q3-maps/viewer.js` + per-game partials | Quake 3, RTCW, ET, JK2, JA |
| `static/unreal-maps/viewer.js` + per-game `materials.js`/`adapter.js` | Unreal, UT, Deus Ex, Quake, Quake 2, Daikatana, Anachronox, SiN, E.Y.E., VTMB, Azrael's Tear, Tribes Vengeance, Tribes Ascend, Nosgoth |
| `static/doom3-maps/` | Doom 3 (+RoE), Quake 4 |
| `static/reflex-maps/` | Reflex Arena |
| `static/diabotical-maps/` | Diabotical |
| `static/n64-maps/` | N64 conversions (local only) |

---

## 1. Best next fixes

### Quick wins (S effort, structural)

| # | Gap | Pages | Fix |
|---|---|---|---|
| 1 | **Doom 3 sky renders black** (RoE `hellsky` draws nothing). `cubemap`/`cameracubemap`/`texgen` parsing is gated on the Q4 `policy` flag, so the D3 sky stage is dropped and the result is a depth-only black pass. ✔︎ | Doom 3, RoE (13+ outdoor maps) | Remove the `policy` gate at `tools/doom3_map_materials.py:87` and `:117`. The viewer's cube pass already exists. Then re-import. |
| 2 | **Diabotical has no sky.** `global.skybox` is set in 78 of 177 maps (corrected by diabotical-fixes; the rest fall back to `default_skybox`), but the importer never reads it and the page clears to flat `0x9fc4e0`. ✔︎ | Diabotical | The sky cubes are in `textures_cubemaps.dbp`, which `write_envmaps` already decodes, and the page already builds a CubeTexture for reflections. Set `scene.background` from it (`static/diabotical-maps/viewer.js:22`). |
| 3 | **Diabotical unlit materials drawn lit.** The importer works out `lit` (`import_diabotical_map.py:246`), but only uses it to add specular and id keys (`surface()`, `:762`). The flag itself is never written to `materials.json`. So `statictranshud`/`statictranstex`/`statictransholo` screens, holograms and glow panes still go through `gameLit()` (`viewer.js:130`) with material class 0, which is lit diffuse, and come out dim. ✔︎ | Diabotical | Write `lit` out and skip `gameLit` when it is false. |
| 4 | **Diabotical props all cast sun shadows.** `castShadow = true` on every prop instance (`viewer.js:338`) ✔︎, but `docs/diabotical-map-viewer.md:35-36` (run 32) says props cast no sun shadow in the game. `force_shadows`/`skip_shadows` are unread. | Diabotical | Likely model: only props with `force_shadows` (20.6k) cast. Check one map in game, then honour `force_shadows`/`skip_shadows`. |
| 5 | **T1/Starsiege haze uses the wrong model.** The viewer uses `Fog(min(h, v-1), v)`, linear and by view depth (`static/t1-maps/viewer.js:628`) ✔︎. The game starts haze at `h²/v`, capped at 0.75·v (DarkStar `simTerrain.cpp:409-419`), uses a t(2−t) curve and radial distance. Example: Raindance hazes from 200 m on the page and 89 m in game. This is the likely cause of the known "Starsiege haze ~15-20% weak". | T1, Starsiege (every map) | Have the importer set a scene flag, and apply it only on the T1/SS path. ES takes its haze value from a different rule, and RB3D already uses its own radial fog. |
| 6 | **Transparency lost on pack-converted DTS shapes.** `texture(name)` defaults to `alpha=False` (`tools/import_t1_map.py:841,905`) ✔︎, so every Starsiege shape and every custom-map T1 shape draws opaque. | T1 custom maps, Starsiege | Pass `alpha=True` for model textures. |
| 7 | **Shared Unreal viewer has no per-frame hook for adapter pages.** `draw()` (`static/unreal-maps/viewer.js:250-255`) ✔︎ only advances Quake, Q2, SiN and UE1 panning. So no adapter page can animate panners, time-varying materials, Source proxies or sky motion. | 9 adapter pages | Add `config.update?.(seconds)` in `draw()`. This is the prerequisite for the animated-material items below. |
| 8 | **Doom 3/Q4 `decal_macro` ignored.** 201 D3 and 526 Q4 materials use it. Only a literal `polygonOffset` is honoured, so decals z-fight and get no sort. | Doom 3, Quake 4 | Expand the macro to `polygonOffset` plus `sort decal` in `doom3_map_materials.py`. |
| 9 | **JA weapon/ammo racks missing (63 props).** `import_ja_map.py:243` keeps the map's `model` key, which points at files that don't exist. The game hardcodes `models/map_objects/kejim/weaponsrack.md3` / `weaponsrung.md3` (OpenJK `g_misc_model.cpp:470,700`). | JA | Add both models to the `defaults` table. |
| 10 | **Reflex `Shootable` (68) and `Turret` (6) entities not drawn.** They are not in `ENTITY_EFFECTS` (`reflex_models.py:55-62`), so `markerOf` returns null. Their effects exist (`shootable_target.effect`, `turret.effect`). | Reflex (training maps, ironguard) | Map both to their effects. |
| 11 | **Q3 and ET libraries have no PVS data.** The code is done (PR #54), but 0/306 Q3 scenes and 0/6 ET scenes have `bspCull`. | Q3, ET | Re-import with `--replace` (needs your OK, since it is a big import). Also fix `docs/q3-map-viewer.md:177`, which still says "culled only by the view". |
| 12 | **Starsiege Snowfall dropped.** `SNOW` blocks are in 8 missions, but `tools/starsiege_mission.py` has no branch for them. The T1 viewer's snow (`viewer.js:559-615`) is already built. | Starsiege | Decode the block and add an SSM branch. |

Smaller S items:
- T1 sun from a type-1 `SimLight` when no `castShadows` Planet exists (3 maps).
- `tcGen vector` data is dropped by `import_q3_map.py:372`, but the viewer already supports it.
- Sky at far depth for Q3/RTCW/ET/JK2 (the `skyAtFarDepth` flag already exists).
- T2 `renderBottomTexture` and `submergeTexture` are not read.

### Big structural gaps (M/L)

| Gap | Pages | Effort | Label |
|---|---|---|---|
| **Fog in Source-engine games:** `env_fog_controller` in 56/60 E.Y.E. maps, `fogenable` in 34/108 VTMB maps. Neither page draws fog. | E.Y.E., VTMB | M (shared fix) | E.Y.E. **New**; VTMB Known (importer gap list) |
| **Quake rerelease worldspawn `fog`:** packs are authored with it, and the game check ran `+r_enablefog 0`. | Quake (qextras, mg1, mg3, ctf) | S–M | **New** |
| **Weather:** T2 Precipitation (17 maps), Lightning (7), FireballAtmosphere (3); ET `atmosphere` rain/snow (radar, railgun); JK2/JA weather zones; RTCW `misc_snow64`; Starsiege SNOW (above). T1 already has it. | T2, ET, JK2/JA, RTCW, SS | M each | T2/ET/SS **New**; JK2/JA Known |
| **Coronas / flares:** Q3 9.5k `MST_FLARE` faces (88 maps), RTCW 3.1k and ET 511 `corona` entities, JA 1.6k; UE1 `bCorona` lights on 79 UT and 38 Unreal maps; D3/Q4 flare deforms. RTCW's importer gives them the wrong skip reason ("no standalone visual"). | Q3 family, Unreal/UT, D3/Q4 | M (one billboard and depth-fade helper could serve several viewers) | Q3 Known; UE1 scale and RTCW reason **New** |
| **Particles / emitters:** T2 ParticleEmissionDummy (112), JK2/JA fx_runner (1.7k), VTMB `env_sprite`/`env_particle` (7.7k), E.Y.E. lightglow/sprite/particles (3.3k), D3 .prt/func_emitter, Q4 BSE (4.3k), UE2/UE3 emitters. Reflex and Diabotical draw theirs, frozen at one moment. | most pages | L overall; M per page | mostly Known |
| **Light grid for models (Q3/RTCW/ET):** every pickup and prop is lit flat. Q3 discards lump 15 at import. The JK2 `LightGrid` class can be ported. | Q3, RTCW, ET | M + re-import | Known |
| **Autosprite / deformed cards:** Q3 autosprite in 103 maps; D3/Q4 sprite/flare/tube deforms on about 28k surfaces. All are drawn as fixed cards. | Q3 family, D3/Q4 | M | Known (in-page) |
| **Dynamic lights not drawn:** T1 dynamic `SimLight` points (about 230, mostly custom maps); RTCW `dlight` (331); ET (9). | T1, RTCW, ET | M | **New** |
| **DTS material flags discarded:** fullbright `ShadingNone` (490 materials in 71 stock shapes: pads, displays) and translucent (76 shapes). IFL texture animation (90 shapes) is discarded too. The exporter JSON keeps no flags. | T1, SS | M (shared exporter) / M–L for IFL | **New** (IFL partly known) |
| **Starsiege campaign units and turrets dropped:** 741 HERC/TANK/FLYR units and 124 turrets. SSM returns None for them. T1's equivalent is drawn behind the NPC toggle. | Starsiege | M | **Done** (PR #104; real library re-imported 2026-10-16: 755 units + 124 turrets on 56 maps) |
| **Quake/Q2 point models:** 743 torch and flame models, plus about 2.5k Q1 and 5k Q2 items, weapons and misc models. Only monsters are placed. | Quake, Quake 2 | M | items Known; torches **New** |
| **UE2/UE3 baked lighting:** TVG BSP lightmaps and UE3 lightmaps are not decoded; there is no local light and no shadows. | TVG, Tribes Ascend, Nosgoth | L | Known (deferred) |
| **Emissive on UE2/UE3 lit materials:** `EmissiveColor` and `SelfIllumination` are recorded but not composited. | TVG, Tribes Ascend, Nosgoth | S–M | Known |
| **Ropes / cables:** `keyframe_rope`/`move_rope` (VTMB 2.7k, E.Y.E. 320). | VTMB, E.Y.E. | S–M | **New** |
| **Doom 3 RoE portal sky renders white** (6 maps). | RoE | M | **New** |
| **D3/Q4 GUI screens blank** (6k surfaces); reflection cubemaps on glass are missing. | D3, Q4 | M each | Known (in-page) |
| **N64 env mapping:** 69 `EnvMapping` materials show a single texel because every `vt` is (0,0). The normals are present. | N64 (PD, GE) | S–M | **New** |
| **T2 interior light states** are discarded (`dif.ts:277`), so animated interior lights stay static. | T2 | M | **New** |
| **Decals/overlays (Source):** `infodecal` VTMB 5.1k, E.Y.E. 6.5k, plus the overlays lump. | VTMB, E.Y.E. | M–L | Known |
| **Sky portals / portals / mirrors:** RTCW `props_skyportal` (24), Q3 portals (29 maps), JA `misc_skyportal`; VTMB `sky_camera` 3D skies (44). | Q3 family, VTMB, E.Y.E. | L | Known |

---

## 2. Cross-cutting view

| Effect | State across viewers |
|---|---|
| **Weather** | Done: T1 (rain and snow with an indoor cutoff). Missing: T2, Starsiege, ET, RTCW, JK2/JA, Daikatana rain. Not in stock data: Reflex, Diabotical. |
| **Fog** | Done: T2, Reflex (incl. height fog), RB3D, Daikatana, Anachronox, JA far clip. Approximated: T1/SS (wrong model, see quick win 5), ES, TVG (first zone only), TAS/NOS (height fog flattened). Missing: E.Y.E., VTMB, Quake rerelease, Q3 fog volumes, D3 fog lights. Declined: UE1 zone fog, RTCW/ET/JK2/JA fog, Q4 fog, Diabotical fog. |
| **Sky** | Done on most pages. Broken: D3 (black), RoE (black or white), Diabotical (none), RB3D (solid haze, known). Partial: Q3 family has no inner box or far depth; Anachronox and SiN are frozen; Source 3D skies are not drawn. |
| **Baked lighting** | Done: T1, T2, Q3 family, Quake family, UE1, Source, Reflex (SH probes), Diabotical, D3/Q4 (per-pixel). Missing: TVG, TAS, NOS. |
| **Dynamic / animated lights** | Lightstyles are held at one value (Quake family, JK2/JA, UE1 averaged). Missing: T1 SimLights, RTCW/ET dlights, T2 interior light states, Diabotical flicker (known). |
| **Shadows** | Reflex sun (2 cascades; spot shadows missing, known); Diabotical (wrong for props, quick win 4); T2 players and vehicles; D3 stencil shadows declined. |
| **Animated textures** | Done: Q3 shader stages, D3/Q4 stages, Quake/Q2 warp, UE1 pan. Missing: adapter pages (no hook, quick win 7), T1/SS IFL, Diabotical scrolling, Reflex `uvSpeed` (particles only). |
| **Coronas / flares / particles** | Mostly missing everywhere except Reflex and Diabotical (frozen) and T1 lens flare. |
| **Movers** | Static at their start pose on every page (known). |
| **Sound** | Not played on any page. T2 deliberately forces audio off. This is probably out of scope; it is listed so the decision is explicit. |

---

## 3. Per-family detail

Rows list gaps only. "Done" features are summarised in a line under each table.

### 3.1 T1 family — Tribes 1, Earthsiege 1/2, Starsiege, Red Baron 3D

| Feature | Pages | Where it is lost | Status | Label |
|---|---|---|---|---|
| Haze start, curve and distance metric | T1, SS (ES by depth only) | `viewer.js:628` (linear `THREE.Fog`, view depth) | wrong model | **New** cause of a known symptom (ss-maps-plan) |
| Dynamic `SimLight` point lights (~209 TRIBES11, 23 stock) | T1 | not read in `import_t1_map.py` (grep `simlight`: 0) | missing | **New** |
| Sun from a type-1 SimLight (BloodRunsCold, Slipstream, Valhalla) | T1 | falls back to 0.6/0.4 with azimuth 0 (`import_t1_map.py:916-918`) | partial | **New** |
| Shape translucency/alpha | T1 custom, SS | `import_t1_map.py:905` uses `alpha=False` | missing on pack models | **New** |
| Fullbright `ShadingNone` / translucent DTS flags | T1, SS | `export_model.py` writes no flags; `viewer.js:153` lights everything | missing | **New** |
| IFL animated textures (90 stock shapes) | T1, SS | discarded by the exporter | missing | partly Known |
| Shape animation, Moveable elevators and doors | T1, SS | static pose | missing | Known (`docs/t1-map-viewer.md` Limits) |
| Item glow and spin (`ItemData lightType`) | T1 | not read | missing (minor) | **New** |
| Campaign units (741) and turrets (124) | SS | drawn behind the NPC toggle (PR #104) | done | **Done** 2026-10-16 |
| Snowfall (8 missions) | SS | no SSM branch | missing | **New** |
| Shape shading constants (ambient .45 / sun .75) | ES | `import_es_map.py:684-685` | approximated | **New** (hypothesis) |
| Night variant, animation, damage | ES | day only | missing | Known (`docs/earthsiege-maps.md`) |
| Animated sky, clouds, cloud shadows | RB3D | `viewer.js:749-756` returns before `buildSky` | missing | Known (`docs/redbaron-maps.md`) |
| Dusk/night palettes, seasons, ground clutter | RB3D | summer only | missing | Known |

Done: sky dome, planets, stars, lens flare, `.dil` lightmaps and animated building lights, terrain lightmap, T1 rain and snow with an indoor cutoff, the sky list, RB3D native fog.

Not applicable (checked): water, lightning, decals, detail textures and env maps. No T1 or SS object class carries them. `hazeVerticalMin/Max` is dead code in the game (`simTerrain.cpp:430`).

Note on the uncommitted WIP:
- `viewer.js:767` declares `const own`, which shadows the sky group `own` from `:299`. It is harmless today.
- `skies.js:181-182` takes the GLSL uniform type from the preset value, so a scalar given where the shader expects a vec3 would silently produce an empty sky.

### 3.2 Tribes 2 (upstream t2-mapper renderer + Skinner adapter)

| Feature | Count | Where | Status | Label |
|---|---|---|---|---|
| Precipitation (rain/snow) | 17 maps | `missionEntityBridge.ts:182` returns null | missing | **New** |
| Lightning | 7 maps | same | missing | **New** |
| FireballAtmosphere | 3 maps | only in `classHierarchy.ts` | missing | **New** |
| ParticleEmissionDummy (smoke, steam, fire) | 112 objects / 9 maps | only in `classHierarchy.ts:132` | missing | **New** |
| Interior light states | all DIFs with states | `dif.ts:277` reads the count and drops it | partial | **New** |
| Mission-placed vehicles | 4 | default null | missing (low) | **New** |
| `renderBottomTexture`, `submergeTexture`, terrain `hazeDistance` | 54 / 26 / 45 | not read | missing (low) | **New** |
| AudioEmitter (670) | 74 maps | audio forced off (`map-client/vite.config.ts:10`) | by design | Known (doc) |
| Sky down face (4 TR2 missions) | — | stock data | data gap | Known (sky-limits-plan, PR #83) |

Done: sky with fog volumes and cloud layers, sun and ambient, terrain with detail texture and lightmap, WaterBlock, interior lightmaps, shapes with IFL and env maps, player and vehicle shadows.

All T2 gaps are in the upstream renderer. Fix them with a Skinner build-time patch (the `vite.config.ts` pattern). Don't post upstream; the user contacts exogen.

### 3.3 Q3 family — Quake 3, RTCW, ET, JK2, JA

| Feature | Pages | Where it is lost | Status | Label |
|---|---|---|---|---|
| Light grid on models | Q, R, E | Q drops lump 15 (`import_q3_map.py:41`); viewer `lightingDiffuse` is flat (`viewer.js:137`) | missing | Known |
| Fog volumes / global fog | all | `viewer.js` has no fog | missing | Q3 Known/open; R/E/K/A Declined |
| Far clip keys (`fogclip`, `farplanedist`, `distancecull`) | R, E, K | not read (only JA's `configureCamera`) | partial | **New** (R/E tied to the declined fog) |
| Sky inner box, sky at far depth | Q, R, E, K | `skyAtFarDepth` is set only by JA | partial | Known |
| Portals, mirrors, sky portals | all | limit only | missing | Known |
| Flares/coronas | all | type-4 faces skipped (`viewer.js:284`); RTCW skip reason wrong (`import_rtcw_map.py:669`) | missing | Known; RTCW reason **New** |
| Autosprite / autosprite2 | Q (103 maps), R, K | `deform()` handles wave/move/bulge only (`viewer.js:151-156`) | missing | Known |
| `tcGen vector` | Q, R, K, A | dropped at `import_q3_map.py:372-373`; the viewer supports it | partial | **New** |
| PVS culling | Q, E | old library data | done in code, missing in data | Known; stale doc line **New** |
| Area portals | all | not read | missing | Known |
| Movers (bobbing 206, rotating 129, pendulum 19, train 28 in Q3) | all | static | approximated | Known |
| Dynamic lights | R (331), E (9) | omitted as "no standalone visual" | missing | **New** |
| Lightstyle animation | K, A | first sample baked | missing | Known |
| Weather | E (`atmosphere`), K/A (76 weather zones in A), R (`misc_snow64`) | not read | missing | E **New**; K/A Known |
| Particles / fx_runner | R, K (559), A (1,184) | omitted | missing | Known |
| Surface-sprite foliage | K, A (E done) | — | missing | Known |
| Glow bloom | A (50 maps) | limit | missing | Known |
| Sun sprite | R (9), E (4) | limit | missing | Known |
| JA gun/ammo racks (63) | A | `import_ja_map.py:243` | missing | **New** |
| Q3 pickups: no game-type filter, no barrels or obelisks | Q | — | partial | Known |
| Sound (target_speaker in the thousands) | all | not read | missing | Known |

Done: the full shader-stage core (blendFunc, animMap, tcMod, rgbGen/alphaGen, deform wave/move/bulge, env tcGen, polygonOffset, detail), lightmaps with overbright, vertex colours, patches at a fixed 4×4, ET foliage and terrain blend.

### 3.4 Unreal-family shared viewer (14 pages)

| Feature | Pages | Where it is lost | Status | Label |
|---|---|---|---|---|
| Per-frame adapter hook | 9 adapter pages | `viewer.js:250-255` | missing (blocks animation) | **New** |
| Distance fog (`env_fog_controller` 56/60) | E.Y.E. | omitted at `import_eye_map.py:194`, absent from its gap list | missing | **New** |
| Distance fog (`fogenable` 34/108) | VTMB | not read | missing | Known (gap list) |
| Worldspawn `fog` | Quake rerelease | not read | missing | **New** |
| Volumetric zone fog (28 Unreal, 14 UT, 28 DX maps) | UE1 | not read | missing | Declined (unreal-maps-plan) |
| TAS/NOS height fog flattened; TVG first zone only | TAS, NOS, TVG | adapters | approximated | Known |
| UE2/UE3 lightmaps, extra lights, shadows | TVG, TAS, NOS | not decoded | missing | Known (deferred) |
| Emissive / SelfIllumination | TVG, TAS, NOS | recorded, not composited | missing | Known |
| Light styles / UE1 LightType | Quake family, UE1 | held at "m" / averaged | approximated | Known |
| Animated textures (UE2/UE3 panners, Source proxies, UE1 AnimNext, Q-family `+0` chains) | all | first frame | partial | Known per game |
| Anachronox/SiN sky motion; Source 3D `sky_camera` skies | Anox, SiN, VTMB (44), E.Y.E. | t=0 / not drawn | partial | Known |
| UE1 `bCorona` coronas | UT (79 maps), Unreal (38) | every Light actor skipped (`import_unreal_map.py:597`) | missing | **New** (scale) |
| Sprites/particles/emitters | all | omitted | missing | Known |
| Ropes/cables | VTMB, E.Y.E. | omitted under a generic reason | missing | **New** |
| Decals/overlays | VTMB, E.Y.E. | not read | missing | Known |
| Quake/Q2 items, weapons, torches | Quake, Q2 | monsters only | partial | items Known; torches **New** |
| UE1 env-map (`PF_Environment`) and DetailTexture | UE1 | dropped by `DRAWN_FLAGS` (`import_unreal_map.py:63`) | missing | **New** (low) |
| Mirrors (`MIRRORED` bit) | UE1 | reaches the viewer, which ignores it (`viewer.js:92`) | missing | Known |
| VTMB prop/NPC lighting (`worldlights` lump unused) | VTMB | — | approximated | Known (vtmb-plan; user kept brightness) |
| Underwater view tint | all but SiN | — | missing (low) | **New** |
| Post-process | TAS, NOS | not read | missing | Known (deferred) |
| Movers | all | static at the start key | approximated | Known |
| Ambient sound | all | not read | missing | **New** (not logged anywhere) |

Done: UE1/Quake-family/Source lightmaps, vertex light, blend and alpha modes, entities and NPCs behind the toggle, Daikatana and Anachronox fog, the UE1 sky zone, Q1/Q2 skies, the TAS dome, the TVG sky zone, terrain layers.

Not a gap: Deus Ex distance fog. Zero of 88 DX maps set `DistanceFog`.

### 3.5 Doom 3 + Quake 4

| Feature | Count | Where it is lost | Status | Label |
|---|---|---|---|---|
| D3 sky (cube/texgen skybox) | 105 surfaces, 13+ maps | gated by `policy` (`doom3_map_materials.py:87,117`) | **black** | **New** |
| RoE portal sky | 17 surfaces, 6 maps | `info_portalsky` read for Q4 only | **white** | **New** |
| `decal_macro` | 201 D3 / 526 Q4 defs | not read | missing | **New** |
| `sort` keyword | — | not read | approximated (low) | **New** |
| Q4 `shear`, expression parse failures, `parallel` lights | low | — | approximated | **New** (low) |
| Reflection cubemaps, mirrors, remote render | 1.1k D3 / 1.5k Q4 | dropped (`DMM:144-145`) | missing | Known (in-page) |
| Sprite/flare/tube/turbulent deforms | ~28k surfaces | noted only | flat cards | Known (in-page) |
| Particles (.prt, emitters, Q4 BSE) | D3 ~2.4k / Q4 4.3k | skipped (`IDM:447-458`) | missing | Known |
| Fog lights / blend lights | 107+225 D3, 232 Q4 | skipped (`IDM:386-387`) | missing | Q4 Declined; D3 needs a decision |
| GUI surfaces | 6.2k | 0 stages | blank | Known (in-page) |
| Heat haze / program stages | ~1.2k | dropped | approximated | Known (in-page) |
| Stencil shadows | — | skipped | missing | Declined (doom3-plan) |
| Movers, Q4 portal fade | — | static / ignored | approximated | Known |

Done: per-pixel point and projected lights with light shaders and falloff, bump and specular, the stage transforms, vertex colour and blend, the Q4 cube sky and portal sky, monsters and items behind the toggle, sound-reactive lights.

### 3.6 Reflex Arena

| Feature | Count | Status | Label |
|---|---|---|---|
| Spot-light shadows (`spotLightCastsShadow`) | 366 entities; Aerowalk wall ~30% too bright | missing | Known (reflex-graphics-plan) |
| `Shootable` / `Turret` entities | 68 / 6 | not drawn or marked | **New** |
| Normal maps | 122 materials | missing | Known (doc Limits) |
| SSAO / HBAO+ | — | missing; whether it is on by default is unverified | Known |
| Item spin and bob, skinned meshes | — | rest pose | Known |
| Particles | 273 placements | frozen at one moment | Known |
| Ribbons, spawners, cloth (effect kinds 6/7/10) | 0 / 0 / 29 flags | missing (low) | **New** |
| Godrays | 49 | skipped | Declined |
| Ambient sounds (effect kind 5) | 313 | missing | **New** (likely out of scope) |
| Sun shadow cascades | 2 vs the game's 4 | approximated | Known |

Done: sky, fog and height fog, sun, the SH probe grid, point lights, emissive materials, liquids, reflection probes, vertex colours, bloom.

### 3.7 Diabotical

| Feature | Count | Status | Label |
|---|---|---|---|
| Sky (`global.skybox`) | 156/177 maps | **missing** (flat colour) | **New** |
| Unlit statictrans materials | ~1.4k shader stages | **drawn lit** | **New** |
| Prop sun shadows | every prop | **wrong** (contradicts the doc) | **New** |
| Fog / vertical fog | 173 / 76 | missing | Declined |
| Light beams (`volumetric` + volume lights) | 1.9k | missing | Declined |
| Light flicker (`visibility_function`) | 4.5k lights | steady | Known |
| Colour-grading LUT | 14 maps | identity only | Known |
| Liquid animation; `shallow_color`, `texture_scale` | 754 liquids | static, keys unread | Known; keys **New** (low) |
| Moving props (`movement`/`action`/`slide`) | ~8.5k | at rest | **New** |
| FBX vertex colours | `*_vercol` models | not read | **New** (impact unmeasured) |
| Normal/detail maps, AO | — | missing | Known |
| `horizon_shade`, `bevel`, `label` | few | missing | **New** (low) |
| Sound | ~1.5k | missing | **New** (likely out of scope) |

Done: sun and shadow colour, the ambient grid, point/spot/capsule lights, decals, pfx and billboards, envmaps, alpha.

Stale doc line: `docs/diabotical-map-viewer.md:5` says "No lights or baked lighting", but lights are drawn now.

### 3.8 N64 conversions

| Feature | Status | Label |
|---|---|---|
| `EnvMapping` materials (69, PD and GE): every `vt` is (0,0), so the surface shows one texel | missing | **New** |
| Fog, combiner modes, scroll, billboards, sky | not in the OBJ/MTL conversions (N/A) | **New** (observation) |

Done: vertex RGBA, opacity, clamp/mirror wrap, cull and decal flags.

---

## 4. Doc and data housekeeping found on the way

- `docs/q3-map-viewer.md:177`: change "culled only by the view" now that PVS has landed.
- `docs/diabotical-map-viewer.md:5`: change "No lights or baked lighting" (lights are drawn now).
- `docs/diabotical-map-viewer.md:35-36` contradicts `viewer.js:338` on prop shadows. One of the two is wrong.
- `import_rtcw_map.py:669`: coronas and dlights are labelled "no standalone visual", which is wrong.
- The E.Y.E. importer's gap list (`import_eye_map.py:257`) doesn't mention fog. The VTMB/E.Y.E. lists don't mention ropes, and no page logs ambient sound as a gap.

## 5. Decisions that need you

1. **Fog for Doom 3:** Q4 fog was declined, but D3 has no recorded decision (332 fog and blend lights).
2. **Sound:** no page plays audio, and T2 forces it off. Confirm this is out of scope so it can leave the gap lists.
3. **Re-imports:** quick wins 1 and 11, and the light grid, need re-imports of the Doom 3, Q3 and ET libraries.
4. **Starsiege campaign units:** should they be drawn behind the NPC toggle, the way T1 `aiplayer` is?
