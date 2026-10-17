# Volumetrics prototype (fog and clouds)

A test bed for volumetric clouds and better fog, built on Skinner Sky. It is built frame rate first: every pass is timed
on the GPU, and the default must cost less than the sky it replaces.

Open `static/volumetrics/index.html` from any server rooted at the repository: the Flask app, or
`python -m http.server` in the repository root (the page reads `/static/three.min.js`, `/static/t1-maps/skies.js` and
`/static/skinner-sky/`). The page draws a test scene: rolling ground 8 km across with stone pillars out to 2 km.

URL options:
- `?w=&h=` sets the drawing size. The default is 2560×1440, whatever the window size.
- `?t=` freezes the clock.
- `?tick` drives the loop with timers, since a hidden browser pane fires no animation frames.
- `?shot` makes `shot()` show the last frame as an image.
- `?mode=layer` starts on the merged sky.
- `?core=` reads another `SkinnerSky.cginc`.

From the console:
- `gpuTimes()` gives the median GPU time of each pass over the last 120 frames, from `EXT_disjoint_timer_query_webgl2`.
- `look(yaw, pitch)` turns the camera.

## How it draws

1. **Sky texture.** The air (Skinner Sky's single scattering, with `SKINNER_SKIP_BEHIND`) and volumetric clouds over it
   are drawn into one small RGBA half-float texture: azimuth across, and elevation from −15° to the zenith up.
   - Alpha is how much of what lies behind the clouds still shows.
   - A band of rows is rewritten each frame (1/8 by default). The whole texture is redrawn on a preset change.
   - Its cost is set by its size, not the screen's.
2. **Clouds** (`SkinnerClouds.cginc`). A slab from `cloudHeight` up `cloudThickness` km is marched in 24 steps, at
   most 25 km of it.
   - Density comes from the preset's shapes (R), with billows (G) and fraying (B) read at points that move with height.
     The noise is 2D: without that, cloud edges become walls, and seen from below all walls point at the zenith.
   - Two looks toward the sun give Beer shadowing: dark cores, and bright sides toward the sun.
   - Skylight is brighter higher up, there is a two-lobe phase, and far clouds fade into the air's haze.
   - Samples are skipped where there is no cloud, and the march stops once 98% of the light is blocked.
3. **Screen sky.** Per pixel, the sky texture is read, then `skinnerBehind` adds the sharp parts: sun disc, stars,
   Milky Way, moon and aurora, dimmed by the air and the clouds.
4. **Fog ring.** A 64×1 texture holds the fog colour by azimuth: the sky texture 2–26° up, averaged round under a
   triangle weight reaching 54° to each side, read every 6° (51 reads). It includes the sun's glow and the clouds.
   - A sharp ring (one direction 3° up) showed through thin fog on the sky as spokes from the zenith.
   - The first ring read every 12° under equal weights over 72° (21 reads). Its reads beat against the ring's 5.6°
     texels, and a low sun's glow stepped in and out at the window's edges: a hard-edged bright band in the fog below
     the sun (Fogbound storm, Stormfront). Read back on Raindance (Fogbound storm), the second difference between
     neighbouring texels under the sun was 0.16–0.17. The triangle brings it to 0.03–0.04, close to a dense 0.5°
     average, and makes the fog under the sun about 10% brighter.
   - Cost, watched 2026-10-17 (test page, 2560×1440, RX 7800 XT, two interleaved runs each): 0.014 ms, up from
     0.007 ms for the 21 reads.
5. **Height fog** (`SkinnerFog.cginc`). Exponential in height, integrated along the view ray in closed form, with no
   march. It is applied after the scene from the depth buffer, together with exposure, ACES and dither.
   - Keys: `fogDensity` (per metre at `fogBase`), `fogHeight` (metres over which it thins by e) and `fogBrightness`.
   - Fog replaces the fixed per-preset `fogColor`: its colour now follows the sky and the clouds in each direction.

The shared core gained one split: `skinnerBehind(d, air, transmit)` holds what `skinnerSky` drew behind the air.
`skinnerSky` output is unchanged. Rendered against the merged core (7 presets × 3 views, 640×360), the mean
difference is 0.000/255 and the maximum is 2/255 (twinkling stars).

## Cost (measured)

Measured 2026-10-15 at 2560×1440, on an RX 7800 XT (ANGLE D3D11, the Claude app's Chromium). These are GPU times per
frame, the median of 200 frames, with the clock frozen.

| Pass | Merged Skinner Sky | Low 512×128, 16 steps | **Default 1024×256, 24 steps** | High 2048×512, 32 steps |
|---|---|---|---|---|
| Screen sky, day / night | 0.78–0.81 / 0.86 | 0.148 / 0.24 | **0.148 / 0.24** | 0.148 / 0.24 |
| Clouds (1/8 of rows per frame) | — | 0.017–0.020 | **0.025–0.028** | 0.053–0.077 |
| Fog ring (21 reads; 51 now: 0.014) | — | 0.006 | **0.006** | 0.006 |
| Height fog (over tone map alone) | — | +0.017 | **+0.017** | +0.017 |

The default costs about **0.20 ms** by day and 0.29 ms at night, against 0.81 ms for the merged sky with no fog. The
saving comes from moving the air (8 steps per pixel) into the sky texture.
- The aurora preset costs less for clouds (0.007–0.029 ms), because it has none.
- Redrawing the whole High texture in one frame costs 0.29 ms. This happens once, when the preset changes.
- On a GPU about 4× slower (GTX 1060 class), expect about 0.8 ms for the default and about 3.2 ms for the merged sky.
  This is scaled from the measurements, not measured.

## Unity (URP)

`static/volumetrics` also holds the Unity side. It uses the same `.cginc` files through a macro header,
`SkinnerVolumetricsHLSL.cginc`.
- **`Skinner/Volumetric Sky`.** The skybox. It reads the sky texture and adds the sun, stars, moon and aurora per pixel.
- **`Hidden/Skinner Volumetrics`.** Three passes: pass 0 draws the sky texture, pass 1 the fog ring, and pass 2 the fog,
  exposure and ACES over the frame. It declares the same properties as the sky, because Unity keeps only declared
  values on a material.
- **`SkinnerVolumetrics.cs`.** Keeps the sky texture and ring up to date, one band of rows per frame. It copies the sky
  material's values onto the work material property by property. `CopyPropertiesFromMaterial` replaces the whole set
  instead. That dropped the fog's keys, and the fog pass read zeros: NaN and black frames.
- **Tools > Skinner Sky.** Make Materials From Presets writes one `Skinner/Volumetric Sky` material per preset. Use
  Selected Material As Sky makes one the skybox, makes the work material, adds URP's Full Screen Pass feature for the
  fog (pass 2, before post-processing, with depth), and adds the component to the scene (`SkinnerVolumetrics.SetUp`).

Checked 2026-10-15 in a scratch URP project, Unity 6000.3.11f1, D3D11, on an RX 7800 XT, at 2560×1440. GPU times per
draw, from n draws in one command buffer synced by a 1-pixel readback: (t(220) − t(20)) / 200, median of 5.

| Pass | Clear afternoon | Stormfront | Moonlit night |
|---|---|---|---|
| Merged `Skinner/Sky` | 0.679 | 0.640 | 0.739 |
| Volumetric sky (screen) | 0.107 | 0.105 | 0.196 |
| Sky texture 1024×256, 1/8 band (whole) | 0.012 (0.098) | 0.009 (0.073) | 0.013 (0.101) |
| Sky texture 512×128, 1/8 band | 0.007 | 0.005 | 0.007 |
| Sky texture 2048×512, 1/8 band (whole) | 0.037 (0.299) | 0.027 (0.213) | 0.036 (0.289) |
| Fog ring | 0.001 | 0.001 | 0.001 |
| Fog, exposure and ACES (pass 2) | 0.066 | 0.063 | 0.064 |

The default costs about **0.19 ms** by day and 0.27 ms at night, against 0.64–0.74 ms for the merged sky, which has no
fog. Whole URP frames in editor batchmode (moonlit, CPU and GPU) took 0.86 ms with volumetrics and fog, and 1.03 ms
with the merged sky.

Against the page at 1280×720, over five views (afternoon, afternoon looking up, golden hour, storm, moonlit), the sky
differs by 0.3–2.2/255 on average. 0.03–3.6% of sky pixels differ by more than 10, at cloud edges and near the sun.
The scene itself is lit differently by design: three.js Lambert against URP Lit.

The fog pass's exposure applies to the whole frame. At night (exposure 6), the scene's lights have to be night lights,
or the ground blows out.
- Evidence: `C:\tmp\skinner-evidence\volumetrics\unity` holds the frames, `timing.txt`, `compare.txt` and `unity.log`.
  `unity-diag-run4` holds the run that found the zeroed values.

## On the Maps pages

The Sky list's Skinner Sky presets on the T1, Starsiege and Earthsiege Maps pages draw this way
(`static/t1-maps/skies.js` `skinner`, shared by those pages). The default tier is fixed: 1024×256, 24 steps, 1/8 of
the rows a frame.
- **Sky.** A ball in the viewer's sky group reads the sky texture and adds `skinnerBehind`, through exposure, ACES and
  2.2. The map's haze takes its colour from that sky, as before.
- **Height fog.** With Fog on, sky and world are drawn into a target with a depth texture, then `SkinnerFog.cginc` puts
  them on screen. `fogBase` counts from the map's first viewpoint height. The world is already display colour, so the
  ring colour goes through the sky's tone map before the mix. The map's own linear haze stays on under it.
- **Cost**, measured 2026-10-17 on Raindance at 2560×1440 (RX 7800 XT, the Claude app's browser pane, in view), whole
  frames with the world drawn, median of 40, the same view (looking along the ground):

  | | Main | This page |
  |---|---|---|
  | The map's own sky | 0.18 ms | 0.18 ms |
  | Skinner Sky, afternoon, Fog off | 0.97 ms | 0.36 ms |
  | Skinner Sky, afternoon, Fog on | 0.97 ms (no height fog) | 0.67 ms |
  | Skinner Sky, moonlit, Fog off | | 0.45 ms |

  On main the per-pixel sky alone was 0.85 ms. The fog pass itself costs about 0.03 ms. Most of the Fog-on cost is the
  fog target's 4× multisampling (+0.28 ms), kept so the world stays antialiased as the canvas is; without it the frame
  was 0.39 ms.
- Timings from a hidden browser pane are not usable: the same views read 2.6–5.7 ms there, and once 0 ms for the world.
- **Presets retuned for these clouds** (2026-10-17, [skinner-sky.md](skinner-sky.md)): the retune costs nothing.
  Measured the same way from Raindance's start viewpoint, this run's own sky read 0.40–0.43 ms, against 0.18 ms in the
  table above, so compare only within the run. All twelve presets took 0.54–0.64 ms with Fog off and 0.75–0.85 ms with Fog on. The old and new values of each preset
  came within 0.05 ms of each other with Fog off. The fog pass is closed form, so preset values don't change its cost.
  A second run had every Fog-on frame jump to 2.5–3.0 ms partway through, old and new presets alike. It stayed after a
  page reload, while Fog-off frames stayed at 0.55 ms, so it was the machine's state at the time. It was not chased.
- The presets carry their own `cloudThickness`, `fogDensity`, `fogHeight` and `fogBrightness` where the defaults
  looked wrong (Stormfront: thick low fog; the night presets: thin). On maps whose own haze is short (CloakOfNight ends
  it at 250 m), that haze takes the sky's colour and whitens the valleys with any preset.

## Limits

- Clouds don't move with the player, and nothing can fly into them. They are kilometres up; the sky texture is drawn
  from the planet's surface.
- Clouds are soft up close: at the default size one texel is about 0.35°. The High tier halves that.
- The noise is 2D. Cloud tops and sides are shaped by height terms, not by 3D noise. Near the horizon at golden hour,
  the clouds streak toward the sun.
- Fog has no light shafts: there is no shadow map in the march. A shaft tier (a low-resolution march of the shadow
  map) would be a separate, measured option.
- The test scene's lighting is rough: the sun's colour comes from the same air model, and the fill light is a guess.
- The HLSL wrapper needs three macros beyond `Skinner/Sky`'s: `vec4`, `atan(y, x)` as `atan2`, and `SAMPLE_LOD` as
  `tex2Dlod` with the v flip. The march loop must sample with an explicit level.
- Evidence: `C:\tmp\skinner-evidence\volumetrics` holds the bench table, the screenshots and the fxc wrapper.
