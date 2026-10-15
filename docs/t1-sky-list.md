# T1 Maps Sky list

The Sky list on the T1, Starsiege and Earthsiege Maps pages swaps the map's own sky for one from any imported map
pack. `/t1-map-skies` (app.py `SKY_PACKS`, `scene_skies`, `t2_mission_sky`) lists them, one group per game in
alphabetical order. The viewer draws each entry with the renderer the entry names: `static/t1-maps/viewer.js` holds the
dome and the Quake III box, and `static/t1-maps/skies.js` holds the others. The first entry, "(mission)'s own
(default)", restores the map's sky. A choice is saved only in the page's local settings, and nothing is written to any
pack. Entry ids are the game plus a hash of the sky, so a saved choice survives re-imports.

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
| Tribes: Vengeance | the same, with models and terrain | `skies.js vengeance`: the same pass, with the level built by its own page's adapter (`tvg-maps/materials.js`) |

**Checks (2026-10-15, user's library, read-only).** I compared skies side by side with their own pages: Bloodlines
pier, Nosgoth MountainVillage, Unreal NyLeve and Vengeance SP-Captured-Towers, the last two seen from their sky
origin. I also looked at E.Y.E. Arcturus, SiN, Quake sky4, Deus Ex UNATCO, UT CTF-Face, Tribes 2 Badlands and Lush, and
a Tribes 2 solid colour. The box orientation of Quake II, Quake III, SiN, Daikatana and Tribes 2 was measured by seam
error along every cube edge (probes in the evidence folder). Tribes 2 needed its top face turned, which brought
Whiteout from 31.6 to 8.9 and Underhill from 13.7 to 1.5. Screenshots are in `C:\tmp\skinner-evidence\more-skies`.

**Limits.**
- Fog under a swapped sky keeps the T1 mission's haze colour.
- An Unreal-engine sky loads that whole level, which takes a few seconds and the memory of the level.
- Vengeance's zone fog applies inside its own sky pass.
- Quake II-family rotating skies (`skyrotate`) stand still.
- Tribes 2 fog bands are not drawn. Its bottom face's turn could not be measured, since the faces are uniform.
- 4 Tribes 2 missions name faces that the extracted data lacks, and 1 mission file is missing; those are not offered.
- E.Y.E.'s flat placeholder skies (40 of 60 maps) are left out.
- Not offered: Tribes: Ascend (excluded for now), Doom 3 (no sky drawn), Quake 4 (camera cubemaps and portal skies,
  not ported), Azrael's Tear (no sky).
- The first scan of a full library takes about 15 s; after that the list is cached until a pack's index changes.
