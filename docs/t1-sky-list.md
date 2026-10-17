# T1 Maps Sky list

The Sky list on the T1, Starsiege and Earthsiege Maps pages swaps the map's own sky for one from any imported map
pack. `/t1-map-skies` (app.py `SKY_PACKS`, `scene_skies`, `t2_mission_sky`) lists them, one group per game in
alphabetical order. The page shows two lists: the game (with its sky count), then that game's skies. One native
list of 900+ entries opened slowly and some browsers left its lower part undrawn until scrolled. Picking a game shows its
first sky. The viewer draws each entry with the renderer the entry names: `static/t1-maps/viewer.js` holds the
dome and the Quake III box, and `static/t1-maps/skies.js` holds the others. The game list's first entry, "(mission)'s own
(default)", restores the map's sky and hides the second list. A choice is saved only in the page's local settings, and nothing is written to any
pack. Entry ids are the game plus a hash of the sky, so a saved choice survives re-imports.

A swapped sky brings its own haze: once it is built, the viewer renders it alone (or its Unreal-engine pass) into a
96x8 target four times, looking round the band from the horizon to 10° above, and the average of those pixels becomes
the fog and background colour (`horizonHaze` in viewer.js). An untextured sky (a Tribes 2 solid colour) gives its
colour. Choosing the mission's own sky again restores the mission's haze. A sky with scrolling clouds is measured once,
when it is chosen.

Quake II, Anachronox and SiN skies that turn (`skyrotate`, `skyaxis`) carry `rotate` and `axis` in their entry's box,
and the box shader turns the look direction about the axis as `quake2-maps/materials.js` does. Only a turning sky gets
the two keys, so every still sky keeps its id.

The list is built by `t1_sky_list` in app.py. A full library takes about 15 s to scan (15.5 s on the user's library,
934 entries), so the result is kept in the data folder as `t1-map-skies.json`, stamped with `SKY_LIST_VERSION`, the
data folder and the mtime of every pack's index (or Tribes 2 manifest). A later start reads it in 0.01 s. The app also
starts the scan in a background thread as it starts, and a request made during that scan waits for it. Bump
`SKY_LIST_VERSION` whenever the entries change.

| Game | What the pack holds | How the T1 page draws it |
|---|---|---|
| Tribes, Starsiege, Earthsiege, Earthsiege 2 | a 16-panel dome (`scene.sky`) | the dome |
| Quake III (+ Team Arena, mods, defrag), RTCW, ET, Jedi Outcast, Jedi Academy | sky shaders (`scene.shaders[].sky`) | Q3 box + cloud stages |
| Quake II, Daikatana, Anachronox, SiN (its three folders) | six env faces (`scene.skybox`) | Q3 box |
| Bloodlines, E.Y.E. | six faces, many DDS | Q3 box through `dds.js`; Bloodlines through its display power (2/3) |
| Tribes 2 | each mission's `Sky` block and material list | Q3 box (top face a quarter turn round), its cloud layers as scrolling stages, or its solid colour |
| Quake | the sky texture's two halves | `skies.js quake1`: two layers scrolling at 16 and 8 texels/s, colormap row 32 |
| Nosgoth | dome meshes flagged sky | `skies.js meshes`: the model buffers, centred on the dome's pivot, lowered to the horizon |
| Unreal, Unreal Tournament, Deus Ex | the level's sky zone (`scene.sky`: SkyZoneInfo origin, rotation, rate) | `skies.js unreal`: the level's geometry and lightmap drawn from the sky camera in a pass behind the T1 world, rotating where the zone does, through the game's display power |
| Skinner Sky (Skinner's own, not a pack) | `static/skinner-sky/presets.json`, which the page reads beside `/t1-map-skies` | `skies.js skinner`: `SkinnerSky.cginc`, the file Unity's shader includes too ([skinner-sky.md](skinner-sky.md)) |
| Tribes: Vengeance | the same, with models and terrain | `skies.js vengeance`: the same pass, with the level built by its own page's adapter (`tvg-maps/materials.js`) |

**Checks (2026-10-15, user's library, read-only).** I compared skies side by side with their own pages: Bloodlines
pier, Nosgoth MountainVillage, Unreal NyLeve and Vengeance SP-Captured-Towers, the last two seen from their sky
origin. I also looked at E.Y.E. Arcturus, SiN, Quake sky4, Deus Ex UNATCO, UT CTF-Face, Tribes 2 Badlands and Lush, and
a Tribes 2 solid colour. The box orientation of Quake II, Quake III, SiN, Daikatana and Tribes 2 was measured by seam
error along every cube edge (probes in the evidence folder). Tribes 2 needed its top face turned, which brought
Whiteout from 31.6 to 8.9 and Underhill from 13.7 to 1.5. Screenshots are in `C:\tmp\skinner-evidence\more-skies`.

**Limits.**
- An Unreal-engine sky loads that whole level, which takes a few seconds and the memory of the level.
- Vengeance's zone fog applies inside its own sky pass.
- Tribes 2 fog bands are not drawn. Its bottom face's turn could not be measured, since the faces are uniform.
- 4 Tribes 2 skies are drawn without their down face, which never shipped (checked against `C:\Dynamix\Tribes2`,
  2026-10-15). SolsDescent, SkinnyDip, FrozenFury and GodsRift use the TR2 mod's `SOM_TR2_Armageddon`, `StonedBlue`
  and `WinterBlue` lists (`TR2final105-client.vl2`), whose down face `lava/skies/volcanic_starrynite_v5_DN` is in no
  archive or loose folder of the install (only `volcanic_starrynite_emap` and the stock `starrynite_v5_dn` are). Their
  entries hold `null` for that face, and the viewer fills it with the sky's horizon haze. Their side faces are blank
  white from a few rows off the horizon down (fogged in the game), so for a sky without a down face everything below
  the horizon takes that fill. Only a missing down face is allowed: a sky missing any other face is still left out.
- T2RPG_WorldMap is not offered, and no re-import would bring it in. Its mission file was extracted (into the
  manifest's `missionPacks`, which the list reads for that mission only), but its `RPG_50.dml` is in neither the
  install nor `ironsphere.vl2` and its loose folder.
- E.Y.E.'s HDR skies (40 of its 60 maps) are tone-mapped at import. `tools/import_eye_map.py` draws each
  `$hdrcompressedtexture` face as rgb × alpha × 8, which is Valve's `sky_hdr_compressed_rgbs` shader (E.Y.E. ships the
  stock `stdshader_dx9.dll`). It uses the map's fixed exposure, clamped, at gamma 2.2. On `sky_day01_01`'s HDR faces this
  reproduces Valve's own LDR faces to 0.35/255. Maps imported before this change keep the flat placeholder
  `eye_sky_plain.png`, which the list leaves out. A re-import with `--replace` brings in their 8 skies (10 in all).
  Most are one flat colour, since E.Y.E.'s scenery is in the 3D skybox, which is not drawn. `forgotten_hdr`, `jian_` and
  `noctis_hdr` are DXT5 with alpha 1, so the rule clamps them to white. `huxley_canal_hdr` is RGBA16F with alpha 0, so it
  comes out black. The shader has no per-format branch, but none of this has been checked in the game. `neweden_hdr`'s
  half-height sides (`scale 1 2`) keep the image on top and the clamped last row below. `noctis_hdrft`'s key has a
  stray tab; it is read as meant.
- Vengeance sky boxes are six clamped faces whose UVs the editor left whole tiles away from 0..1 (or at -1..0), which
  smeared the edge texels into streaks, on its own page as well. `tvg-maps/materials.js prepareGeometry` shifts each
  clamped group back into one tile at load (the shared Unreal viewer and `skies.js unreal` call it). Regicide's bottom
  face repeats (not clamped) and keeps a pale band low on the horizon.
- Not offered: Tribes: Ascend (excluded for now), Doom 3 (no sky drawn), Quake 4 (camera cubemaps and portal skies,
  not ported), Azrael's Tear (no sky).
- A pack whose maps change without its index changing keeps the kept list until another index changes.

## Weather list

The Weather control works the same way: the game list ("(mission)'s own" by default, None, then one group per game),
then that game's weathers. `/t1-map-weather` comes from the same scan and cache file (`scene_weather`,
`t2_mission_weather`), and each distinct weather is listed once with its maps. Tribes, Starsiege and Earthsiege weather is
drawn by the T1 box unchanged. Quake III family weather (Q3, RTCW, ET, JK2, JA) goes through the same box with its own
counts, speeds, streak lengths and colours, at 1/32 m per game unit. RTCW's `misc_snow` columns are left out because they
belong to one spot. Tribes 2 Precipitation (the object, its `weather.cs` datablock and the Sky's wind) is drawn by a
plain-JS copy of `map-client/environment.tsx` Precipitation. Every kind is hidden inside buildings by the T1 shelter test.
The importer stores shelters for every mission, so a dry map needs a re-import before borrowed weather stays out of its
buildings. Not offered yet: Bloodlines' rain that follows the player (sm_hub_1, particles.js) and its trigger rain boxes.

Storms come in the same list, with or without rain or snow of their own; one without blows or strikes over the map's own.
Tribes 2 Lightning objects (their rate, width, colours, box size and `lightning.cs` strike texture, not their place) are
the T2 page's bolts, landing in the box centred on the camera, between 100 m off and 80% of the view. Tribes 2
FireballAtmosphere entries carry their datablock chain (Debris, trail and explosion emitters, particles) as numbers; the
fireballs fall about the camera and burst on the terrain. JA and JK2 maps carry `storm` from the importer
(`import_jk2_map.storm`): fx_wind's normal, constant and gusting zones, the wind a plain fx_rain (and every JK2 fx_rain or
fx_snow) adds, and JA fx_rain's lightning. The wind pushes whatever falls at its terminal speed (7/3 force / mean mass
7.5, at 1/32 m a unit); JA lightning flickers the haze to the flash colour and draws the env/huge_lightning bolt. On the
stock maps: hoth2 (snow, constant + gusting), t1_rail (heavy rain, constant wind, lightning), t2_wedge (wind only),
yavin_swamp (rain, wind). Neither JA/JK2 Maps page draws wind or lightning yet. Bolts and fireballs cost nothing between
strikes: their buffers upload only the part in use.
