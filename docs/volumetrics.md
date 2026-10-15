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
4. **Fog ring.** A 64×1 texture holds the fog colour by azimuth: the sky texture averaged over 72° of azimuth and 2–26°
   up (21 reads). It includes the sun's glow and the clouds.
   A sharp ring (one direction 3° up) showed through thin fog on the sky as spokes from the zenith.
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
| Fog ring | — | 0.006 | **0.006** | 0.006 |
| Height fog (over tone map alone) | — | +0.017 | **+0.017** | +0.017 |

The default costs about **0.20 ms** by day and 0.29 ms at night, against 0.81 ms for the merged sky with no fog. The
saving comes from moving the air (8 steps per pixel) into the sky texture.
- The aurora preset costs less for clouds (0.007–0.029 ms), because it has none.
- Redrawing the whole High texture in one frame costs 0.29 ms. This happens once, when the preset changes.
- On a GPU about 4× slower (GTX 1060 class), expect about 0.8 ms for the default and about 3.2 ms for the merged sky.
  This is scaled from the measurements, not measured.

## Limits

- Clouds don't move with the player, and nothing can fly into them. They are kilometres up; the sky texture is drawn
  from the planet's surface.
- Clouds are soft up close: at the default size one texel is about 0.35°. The High tier halves that.
- The noise is 2D. Cloud tops and sides are shaped by height terms, not by 3D noise. Near the horizon at golden hour,
  the clouds streak toward the sun.
- Fog has no light shafts: there is no shadow map in the march. A shaft tier (a low-resolution march of the shadow
  map) would be a separate, measured option.
- The test scene's lighting is rough: the sun's colour comes from the same air model, and the fill light is a guess.
- In this checkout, the page is the only user. The T1 Maps pages still draw the merged sky, and Unity still has
  `Skinner/Sky` only. The HLSL side compiles offline with fxc (ps_5_0, Unity 6000.3.11f1 CGIncludes, `/Gec`): the
  sky-texture pass, the screen sky and the fog. It has not run in Unity yet.
  The HLSL wrapper needs three macros beyond `Skinner/Sky`'s: `vec4`, `atan(y, x)` as `atan2`, and `SAMPLE_LOD` as
  `tex2Dlod` with the v flip. The march loop must sample with an explicit level.
- Evidence: `C:\tmp\skinner-evidence\volumetrics` holds the bench table, the screenshots and the fxc wrapper.
