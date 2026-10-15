# Skinner Sky

Skinner's own sky format. It is made to look good, carry over to Unity unchanged and stay cheap there. A sky is a
preset of plain numbers, drawn by one shader on a sphere round the camera. The shader has no textures besides one
512² noise image, no precomputed tables, no render targets and no extra passes.

On the T1, Starsiege and Earthsiege Maps pages, the Sky list has a **Skinner Sky** group with seven presets: Clear
afternoon, Golden hour, Stormfront, Blue hour, Moonlit night, Aurora and Alien dusk.

## Files (`static/skinner-sky`)

| File | What it is |
|---|---|
| `presets.json` | The presets: a list of flat objects of numbers and three-number lists, plus `id` and `name` |
| `SkinnerSky.cginc` | The whole look, `skinnerSky(direction)`. The page and Unity both compile this same file |
| `SkinnerSkyNoise.png` | The noise, as linear data: R cloud shapes (Perlin-Worley), G billows, B fine fBm. `tools/make_sky_noise.py` writes it (seeded) |
| `SkinnerSky.shader` | Unity's skybox shader `Skinner/Sky`: properties, HLSL macros, then `#include "SkinnerSky.cginc"` |
| `SkinnerSkyPresets.cs` | Unity editor menu: one material per preset, and "use as sky" |

The page wraps the core in GLSL (`static/t1-maps/skies.js` `skinner`). Unity wraps it in HLSL. The core is written in
what both languages accept. On the HLSL side, `vec2`, `vec3`, `mix`, `fract` and `texture2D` are macros. One-number
vector constructors such as `vec3(0.)` are avoided, because HLSL rejects them. `tests/test_skinner_sky.py` checks that
the page's defaults, the Unity properties and HLSL uniforms, and the preset keys all match.

## Conventions

- Directions are z up, with x east and y north. An azimuth is in degrees from north toward east; an elevation is in
  degrees above the horizon. Unity's +Z is north and +X is east, and the shader swizzles its y-up direction.
- Colours are linear multipliers, not sRGB. The exception is `fogColor`, which is a display colour.
- Distances are in km and wind is in km/s.
- Output is exposure, then ACES (Narkowicz fit), then the display's 2.2, with half a step of dither.

## Keys (default)

| Key | Default | Meaning |
|---|---|---|
| `sunAzimuth`, `sunElevation` | 200, 35 | Where the sun is |
| `sunColor`, `sunIntensity` | (1, .98, .95), 20 | Sunlight above the air |
| `sunSize` | 1.2 | Disc diameter in degrees (the real sun is 0.53) |
| `moonAzimuth`, `moonElevation`, `moonSize` | 20, −30, 3.5 | The moon, or a planet when it is large. Its phase follows the sun's direction |
| `moonColor`, `moonIntensity` | (.9, .92, 1), 0 | Disc brightness, and the moonlight scattered by the air at night. 0 = no moon |
| `rayleigh` | (1, 1, 1) | Scale of Earth's air scattering per channel. Changing it changes the planet's sky colour |
| `ozone` | 1 | Ozone absorption: it keeps the zenith blue at dusk |
| `mie`, `mieG` | 1, .8 | Haze amount, and how strongly it scatters forward (the glow round the sun) |
| `groundColor` | (.25, .24, .22) | What shows below the horizon, under the haze |
| `nightColor` | (.0004, .0007, .0014) | Airglow: the sky's floor at night, brighter low down |
| `exposure` | 1.5 | Exposure. Night presets use 6 to 12 |
| `starIntensity`, `starDensity` | 0, 250 | Stars, one per cell of a grid of that many cells per radian. Twinkle, coloured |
| `milkyWay` | 0 | A band of glow, with denser stars along it |
| `cloudCoverage`, `cloudSoftness` | .5, .15 | Heaped clouds: how much of the sky, and how soft their edges |
| `cloudScale`, `cloudHeight`, `cloudSpeed` | 20, 2, (.02, .005, 0) | Noise tile size (km), layer height (km), wind (km/s) |
| `cloudColor`, `cloudDarkness`, `cloudOpacity` | (1, 1, 1), 1, 1 | Tint, how dark thick clouds get, overall opacity |
| `cirrusCoverage`, `cirrusScale`, `cirrusSpeed` | .3, 150, (.04, .01, 0) | Thin streaks 9 km up |
| `auroraIntensity`, `auroraColor`, `auroraColor2` | 0, (.1, 1, .4), (.6, .2, 1) | Curtains 100 to 175 km up, low colour to high colour |
| `fogColor` | (.9, .92, .93) | Unity only: `RenderSettings.fogColor`. The page measures its own haze |

## How it draws

1. **Air**: single scattering of sun and moon light (Rayleigh, Mie and ozone, Earth's coefficients), along the view
   ray in 8 steps spaced closer together near the eye. Each step is lit through the air above it, using a shell-chord
   approximation of Chapman's function and the planet's shadow. This gives a blue day, orange sunsets with a blue
   zenith, the pink band and Earth's shadow opposite a low sun, and a blue moonlit night, all from the sun's position.
   Below the horizon, the ray is held level and blended toward the lit ground colour.
2. **Behind the air**: stars, the Milky Way glow, and the moon or planet, dimmed by the air's transmittance. Stars and
   aurora fade as the sky gets brighter. The sun disc has limb darkening.
3. **Cirrus**, then **clouds**, as layers on shells at their height (so they flatten toward the horizon and fade into
   haze with distance). The clouds use four noise reads: shape, billows, fraying wisps, and the shape one step toward
   the sun. Light through their thickness (Beer) makes cores dark and edges bright. A silver lining comes from the
   forward phase. Sunlight reaches them through the air at their height, so they turn orange and pink at sunset.
   Thick cloud also blocks skylight.

**Cost.** fxc compiles the Unity pixel shader to about 817 instruction slots (ps_5_0, counting every branch). A sky
reads the noise at most 11 times: 6 with clouds and cirrus, 1 more for the moon and 4 for aurora. Branches skip
parts whose preset is off. It is one draw call over the background pixels, with no setup work. At 1440p that should
be well under a millisecond on a current GPU (estimated, not measured).

## Unity

1. Copy `static/skinner-sky` into `Assets`. `SkinnerSkyPresets.cs` imports the noise as linear, repeating and
   uncompressed.
2. Choose **Tools > Skinner Sky > Make Materials From Presets**. This writes `Sky <name>.mat` beside `presets.json`.
   Run it again after editing the presets; it updates the materials in place.
3. Select a material, then choose **Tools > Skinner Sky > Use Selected Material As Sky**. This sets the skybox and the
   fog colour, turns the Sun Source (or the first directional light) to the preset's sun, and updates the environment
   lighting.

The shader is written for Built-in and URP (a plain CG skybox, as Unity's own procedural sky is) and for linear and
gamma colour space; see the check below for what has been run. If the camera does
its own tone mapping (URP post-processing with ACES), switch off the material's **tonemap** toggle. The shader then
outputs HDR light × exposure, and the sun disc can drive bloom.

**Check (2026-10-15).** The shader body was compiled with fxc 10.0.19041 (ps_5_0 and vs_5_0) against Unity
6000.3.11f1's own `CGIncludes`, with the `/Gec` flag that Unity's headers need. Both compiled with no errors or
warnings. It has not been opened in the Unity Editor, and no Unity frame has been compared with the page.

## Limits

- The fog colour is fixed per preset. Each was measured from the page's own haze probe at one moment
  (`horizonHaze`, which includes the clouds of that moment).
- The observer stands 2 m above the ground. Flying higher does not change the sky, and nothing passes through the
  clouds: they are a layer, not a volume.
- The sun does not move during the day. A preset is one moment, and only clouds, stars and aurora move.
- At high values, ACES turns the sun disc and lit cloud tops white.
- Presets are hand-tuned on Raindance in the page's browser pane. The aurora and Milky Way shapes come from the same
  noise tile, so they repeat over very large distances.
